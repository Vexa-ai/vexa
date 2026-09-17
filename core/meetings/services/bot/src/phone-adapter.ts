/**
 * Phone adapter (dial-in) — the NON-BROWSER capture source.
 *
 * ╔══════════════════════════════════════════════════════════════════════════════════════════╗
 * ║ SPIKE. Everything below the transport is real and offline-provable; the transport itself  ║
 * ║ is absent. There is no SIP stack, no trunk, no provider and no credential in this repo:   ║
 * ║ this file takes audio that ALREADY EXISTS as bytes (a WAV, or a local softphone's capture)║
 * ║ and proves the transcription pipeline accepts a source that never went through a browser. ║
 * ║ What a real call still needs: docs/adr/0036-dial-in-bridge-phone-platform.md.             ║
 * ╚══════════════════════════════════════════════════════════════════════════════════════════╝
 *
 * The Local room case of Vexa Rooms: people are in a room with a conference speakerphone and
 * there is no Meet/Teams/Zoom call at all. The phone dials a number; the call is the meeting.
 *
 * Where it sits. Every existing platform reaches the pipeline through `capture-bridge.ts`:
 * Playwright page → page-side capture → PCM frames over the bridge → `BotPipeline.feedAudio`.
 * This file is the twin of that bridge for a source with no page. It ends at the SAME seam, and
 * that is the whole point of the spike: the engine below `feedAudio` knows nothing about browsers.
 *
 * Attribution. A speakerphone beamforms, echo-cancels and MIXES every microphone in the room
 * before the call leaves it, so the call carries exactly one stream and one speaker: the room.
 * Diarizing it would invent divisions the audio no longer contains. So the adapter feeds ONE
 * channel under the room's own label — per-channel lane, one channel, name known at capture.
 *
 * Feature-flagged: VEXA_PHONE_PLATFORM=1. Unset, `pumpPhoneCall` refuses to run.
 */
import {
  PHONE_PLATFORM,
  phonePlatformEnabled,
  type CapturePlatform,
  type Invocation,
} from './config.js';
import { createBotPipeline, type PipelineInvocation, type Transcribe } from './pipeline.js';
import type { BotPipeline } from './pipeline.js';
import type { TranscriptSink } from './ports.js';
import type { SpeakerStreamManagerConfig } from '@vexa/gmeet-pipeline';

/** The room is one channel, always this one. A dial-in call has no second track to give a 1. */
export const PHONE_CHANNEL = 0;

/** Whisper's input rate. Narrowband telephony (8 kHz) is upsampled to it before it reaches STT. */
export const PHONE_TARGET_SAMPLE_RATE = 16_000;

/** Frame size the adapter pumps. 20 ms is the RTP packet; the pipeline's turn gating works on
 *  chunks, so frames are coalesced to this before they cross — the same 200 ms the browser
 *  capture bridge emits, so both sources present the engine with the same granularity. */
export const PHONE_FRAME_MS = 200;

/** One dial-in call: what was dialled, which call it is, and who the audio belongs to. */
export interface PhoneCall {
  /** the dial-in address (@vexa/join `parsePhoneTarget(...).address`) — `+15551234567`, `room-3@calls.example.org` */
  address: string;
  /** `native_meeting_id` for THIS call (@vexa/join `phoneNativeMeetingId`) — address + call discriminator */
  nativeMeetingId: string;
  /** the room's own label — the speaker every segment of this call is attributed to. It comes
   *  from the room's registration (the DID's name in the deployment), never from the audio. */
  roomLabel: string;
  /** epoch ms of the call's first audio sample; frame timestamps are offsets from it */
  startedAt: number;
}

/** 16-bit linear PCM. */
const WAVE_FORMAT_PCM = 1;
/** IEEE float. */
const WAVE_FORMAT_IEEE_FLOAT = 3;
/** G.711 µ-law — what a PSTN trunk actually delivers, so the decoder handles it natively. */
const WAVE_FORMAT_MULAW = 7;
/** WAVE_FORMAT_EXTENSIBLE: the real format lives in the GUID's first two bytes. */
const WAVE_FORMAT_EXTENSIBLE = 0xfffe;

export interface DecodedWav {
  sampleRate: number;
  channels: number;
  /** interleaved samples, normalized to [-1, 1] */
  pcm: Float32Array;
}

/**
 * Decode a RIFF/WAVE buffer to float PCM. Handles the three formats a dial-in path actually
 * produces: 16-bit linear PCM (a recorded fixture), 32-bit IEEE float (a softphone's capture),
 * and 8-bit G.711 µ-law (a PSTN trunk). Anything else is REFUSED by name rather than decoded as
 * noise — a silently misread header is a transcript of nothing, which is far worse than a throw.
 */
export function decodeWav(bytes: Uint8Array): DecodedWav {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (off: number) => String.fromCharCode(bytes[off], bytes[off + 1], bytes[off + 2], bytes[off + 3]);
  if (bytes.byteLength < 12 || tag(0) !== 'RIFF' || tag(8) !== 'WAVE') {
    throw new Error('decodeWav: not a RIFF/WAVE buffer');
  }

  let format = -1, channels = 0, sampleRate = 0, bitsPerSample = 0;
  let dataOff = -1, dataLen = 0;
  let off = 12;
  while (off + 8 <= bytes.byteLength) {
    const id = tag(off);
    const size = view.getUint32(off + 4, true);
    const body = off + 8;
    if (id === 'fmt ') {
      format = view.getUint16(body, true);
      channels = view.getUint16(body + 2, true);
      sampleRate = view.getUint32(body + 4, true);
      bitsPerSample = view.getUint16(body + 14, true);
      if (format === WAVE_FORMAT_EXTENSIBLE && size >= 40) format = view.getUint16(body + 24, true);
    } else if (id === 'data') {
      dataOff = body;
      dataLen = Math.min(size, bytes.byteLength - body);
    }
    off = body + size + (size % 2); // chunks are word-aligned
  }
  if (format < 0 || dataOff < 0) throw new Error('decodeWav: missing fmt or data chunk');
  if (!channels || !sampleRate) throw new Error('decodeWav: fmt chunk declares no channels or sample rate');

  let pcm: Float32Array;
  if (format === WAVE_FORMAT_PCM && bitsPerSample === 16) {
    const n = Math.floor(dataLen / 2);
    pcm = new Float32Array(n);
    for (let i = 0; i < n; i++) pcm[i] = view.getInt16(dataOff + i * 2, true) / 32768;
  } else if (format === WAVE_FORMAT_IEEE_FLOAT && bitsPerSample === 32) {
    const n = Math.floor(dataLen / 4);
    pcm = new Float32Array(n);
    for (let i = 0; i < n; i++) pcm[i] = view.getFloat32(dataOff + i * 4, true);
  } else if (format === WAVE_FORMAT_MULAW) {
    pcm = new Float32Array(dataLen);
    for (let i = 0; i < dataLen; i++) pcm[i] = muLawToFloat(bytes[dataOff + i]);
  } else {
    throw new Error(`decodeWav: unsupported format ${format} @ ${bitsPerSample} bits — dial-in decodes 16-bit PCM, 32-bit float, or G.711 µ-law`);
  }
  return { sampleRate, channels, pcm };
}

/** G.711 µ-law byte → float. The exact ITU-T G.711 expansion, not an approximation. */
export function muLawToFloat(byte: number): number {
  const u = ~byte & 0xff;
  const sign = u & 0x80;
  const exponent = (u >> 4) & 0x07;
  const mantissa = u & 0x0f;
  let magnitude = ((mantissa << 1) + 33) << exponent;
  magnitude -= 33;
  const sample = sign ? -magnitude : magnitude;
  return Math.max(-1, Math.min(1, sample / 32124));
}

/** Interleaved multi-channel → mono by averaging. A conference phone is mono; a softphone
 *  capture may not be, and a stereo stream fed as mono would halve the apparent sample rate. */
export function toMono(pcm: Float32Array, channels: number): Float32Array {
  if (channels <= 1) return pcm;
  const n = Math.floor(pcm.length / channels);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let c = 0; c < channels; c++) s += pcm[i * channels + c];
    out[i] = s / channels;
  }
  return out;
}

/** Linear resample to 16 kHz. Telephony is narrowband 8 kHz; STT wants 16 kHz. Linear is enough
 *  for an upsample of band-limited speech and keeps the spike free of a DSP dependency — an
 *  anti-aliased polyphase filter belongs in the real transport, and the ADR says so. */
export function resampleTo16k(pcm: Float32Array, sampleRate: number): Float32Array {
  if (sampleRate === PHONE_TARGET_SAMPLE_RATE) return pcm;
  if (!sampleRate) throw new Error('resampleTo16k: sample rate is zero');
  const ratio = PHONE_TARGET_SAMPLE_RATE / sampleRate;
  const n = Math.max(1, Math.round(pcm.length * ratio));
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const src = i / ratio;
    const i0 = Math.floor(src);
    const i1 = Math.min(i0 + 1, pcm.length - 1);
    const t = src - i0;
    out[i] = pcm[i0] * (1 - t) + pcm[i1] * t;
  }
  return out;
}

/** WAV bytes → exactly what the pipeline wants: mono float PCM at 16 kHz. */
export function wavToPipelinePcm(bytes: Uint8Array): Float32Array {
  const { pcm, channels, sampleRate } = decodeWav(bytes);
  return resampleTo16k(toMono(pcm, channels), sampleRate);
}

/** Split 16 kHz mono PCM into the frames the adapter pumps (a short tail frame is kept). */
export function framesOf(pcm: Float32Array, frameMs: number = PHONE_FRAME_MS): Float32Array[] {
  const size = Math.max(1, Math.round((PHONE_TARGET_SAMPLE_RATE * frameMs) / 1000));
  const frames: Float32Array[] = [];
  for (let i = 0; i < pcm.length; i += size) frames.push(pcm.subarray(i, Math.min(i + size, pcm.length)));
  return frames;
}

export interface PhonePumpOptions {
  frameMs?: number;
  /** ms of wall clock between frames. A live call arrives in real time, so the default IS the
   *  frame duration; an offline replay may compress it. The pipeline's turn gating is a real
   *  clock, so a pump with no pacing hands it a whole call in one instant and gates nothing. */
  paceMs?: number;
  /** called after each frame crosses — the adapter's own observation point */
  onFrame?: (index: number, tsMs: number, samples: number) => void;
}

export interface PhonePumpResult {
  framesFed: number;
  samplesFed: number;
  /** audio duration pumped, ms (not wall clock) */
  audioMs: number;
}

/**
 * Pump one call's audio into the pipeline, frame by frame, as the capture bridge would.
 *
 * `tsMs` is epoch ms — the SAME clock domain the browser bridge stamps — because the engine's
 * turn windows and every downstream absolute timestamp are computed in it. A pump that stamped a
 * relative clock would produce a transcript dated to 1970.
 */
export async function pumpPhoneCall(
  pipeline: BotPipeline,
  pcm16k: Float32Array,
  call: PhoneCall,
  opts: PhonePumpOptions = {},
): Promise<PhonePumpResult> {
  if (!phonePlatformEnabled()) {
    throw new Error('pumpPhoneCall: the dial-in platform is off — set VEXA_PHONE_PLATFORM=1 to enable it');
  }
  if (!call.roomLabel) {
    throw new Error('pumpPhoneCall: a call needs a roomLabel — the room IS the speaker, and an unnamed room produces an unattributable transcript');
  }
  const frameMs = opts.frameMs ?? PHONE_FRAME_MS;
  const paceMs = opts.paceMs ?? frameMs;
  const frames = framesOf(pcm16k, frameMs);

  let samplesFed = 0;
  let offsetMs = 0;
  for (let i = 0; i < frames.length; i++) {
    const frame = frames[i];
    const tsMs = call.startedAt + offsetMs;
    pipeline.feedAudio(PHONE_CHANNEL, call.roomLabel, frame, tsMs);
    samplesFed += frame.length;
    opts.onFrame?.(i, tsMs, frame.length);
    offsetMs += (frame.length / PHONE_TARGET_SAMPLE_RATE) * 1000;
    if (paceMs > 0) await new Promise((r) => setTimeout(r, paceMs));
  }
  return { framesFed: frames.length, samplesFed, audioMs: Math.round(offsetMs) };
}

/**
 * Build the pipeline for a dial-in call. The lane pick is `createBotPipeline`'s, on
 * `platform: 'phone'` — one per-channel track named at capture, no diarizer.
 *
 * `meetingUrl` carries the dial-in address as a `tel:`/`sip:` URI so every consumer that logs or
 * displays the meeting's origin shows what was dialled, exactly as a web meeting shows its link.
 */
export function createPhonePipeline(
  call: PhoneCall,
  sink: TranscriptSink,
  opts: {
    botName?: string;
    language?: string;
    transcribe?: Transcribe;
    config?: SpeakerStreamManagerConfig;
    onError?: (e: unknown) => void;
    /** the rest of an invocation (transcription service, redis, …) when the caller has one */
    invocation?: Partial<Invocation>;
  } = {},
): BotPipeline {
  if (!phonePlatformEnabled()) {
    throw new Error('createPhonePipeline: the dial-in platform is off — set VEXA_PHONE_PLATFORM=1 to enable it');
  }
  const inv: PipelineInvocation = {
    ...(opts.invocation as Omit<Invocation, 'platform'> | undefined),
    platform: PHONE_PLATFORM as CapturePlatform,
    meetingUrl: phoneMeetingUrl(call),
    nativeMeetingId: call.nativeMeetingId,
    botName: opts.botName ?? call.roomLabel,
    redisUrl: opts.invocation?.redisUrl ?? '',
    language: opts.language ?? opts.invocation?.language ?? null,
  };
  return createBotPipeline(inv, sink, {
    transcribe: opts.transcribe,
    config: opts.config,
    onError: opts.onError,
  });
}

/** The `tel:`/`sip:` URI for a call's address — the dial-in equivalent of a meeting link. */
export function phoneMeetingUrl(call: PhoneCall): string {
  return call.address.includes('@') ? `sip:${call.address}` : `tel:${call.address}`;
}

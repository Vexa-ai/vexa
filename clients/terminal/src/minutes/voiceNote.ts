/** VOICE NOTES (#1783) — record in Minutes, transcribed by the same pipeline as a meeting.
 *
 *  A voice note is a meeting with no platform to join. Starting one asks for a `voice_note` bot
 *  (`POST /api/bots`), which makes the meeting row; the microphone is then captured here, resampled
 *  to 16 kHz mono, framed as capture.v1 audio frames (the same wire the extension and the bot use) and
 *  streamed over the same-origin socket `/voice-notes/{id}/stream` — the terminal server adds the
 *  person's key and relays it through the gateway to meeting-api, which buffers it for the bot. The
 *  transcript arrives on the meeting page like any meeting's. Stop sends `{"type":"stop"}`; the bot
 *  finalizes the note like a meeting end.
 *
 *  One recorder per tab, held at module level so it survives the meeting page re-rendering; React
 *  reads it through `useVoiceNote()`. The browser pieces (microphone, AudioContext, WebSocket, fetch)
 *  are injected, so the framing and the protocol run under test without a browser. */
import { useSyncExternalStore } from "react";

/** The window event that opens the recorder in this tab: raised by the empty chat's "Record a voice
 *  note" chip and by the agent's `record_voice_note` tool call (surfaces/toolUiEvents.ts); the shell
 *  answers it by starting a note. */
export const VOICE_NOTE_OPEN = "vexa:voice-note-open";

export const SAMPLE_RATE = 16_000;
/** 100 ms of 16 kHz audio per frame. */
export const FRAME_SAMPLES = 1_600;

export type VoiceNotePhase = "idle" | "starting" | "recording" | "stopping" | "stopped" | "error";

export interface VoiceNoteState {
  phase: VoiceNotePhase;
  meetingId?: string;        // the meeting row id
  nativeId?: string;         // the voice note's id (`vn-…`)
  sentSec: number;           // seconds of audio sent
  receivedSec: number;       // seconds meeting-api acknowledged
  maxSec?: number;
  level: number;             // 0..1 RMS of the last chunk, for the meter
  paused: boolean;
  error?: string;
  /** Said while recording when the microphone delivers nothing (cleared when audio comes). */
  warning?: string;
}

/** How long the socket may take to say `ready` before the note fails with words instead of sitting. */
export const READY_TIMEOUT_MS = 10_000;
/** How long the microphone may deliver nothing before the person is told. */
export const NO_AUDIO_WARN_MS = 5_000;
export const NO_AUDIO_WARNING = "No audio from your microphone. Check that it is not muted or used by another app.";

const IDLE: VoiceNoteState = { phase: "idle", sentSec: 0, receivedSec: 0, level: 0, paused: false };

/** capture.v1 no-name audio frame: Int32 track (LE), Float64 capture epoch ms (LE), Float32 PCM (LE).
 *  Byte-identical to `@vexa/capture-codec` `encodeAudioFrame(track, ts, pcm)`. */
export function encodeAudioFrame(track: number, tsMs: number, pcm: Float32Array): ArrayBuffer {
  const buf = new ArrayBuffer(12 + pcm.length * 4);
  const view = new DataView(buf);
  view.setInt32(0, track, true);
  view.setFloat64(4, tsMs, true);
  for (let i = 0; i < pcm.length; i++) view.setFloat32(12 + i * 4, pcm[i], true);
  return buf;
}

/** A streaming linear resampler: feed chunks at `fromRate`, get samples at `toRate`, phase kept
 *  across chunks so the joins are seamless. */
export function createResampler(fromRate: number, toRate = SAMPLE_RATE) {
  const step = fromRate / toRate;
  let t = 0;                  // next output position, in samples of `ext` (below)
  let prev: number | null = null;
  return (input: Float32Array): Float32Array => {
    if (fromRate === toRate) return input.slice();
    if (!input.length) return new Float32Array(0);
    // ext = [last sample of the previous chunk, ...this chunk], so interpolation spans the join.
    const ext = prev === null ? input : Float32Array.of(prev, ...input);
    const last = ext.length - 1;
    const out: number[] = [];
    while (t <= last) {
      const i = Math.floor(t), frac = t - i;
      const a = ext[i], b = ext[Math.min(i + 1, last)];
      out.push(a + (b - a) * frac);
      t += step;
    }
    t -= last;
    prev = ext[last];
    return Float32Array.from(out);
  };
}

/** Cuts a sample stream into FRAME_SAMPLES frames, each stamped with its first sample's capture time
 *  (epoch ms), counted from the start of capture so a stalled main thread never compresses time. */
export function createFramer(startEpochMs: number, emit: (pcm: Float32Array, tsMs: number) => void) {
  let pending = new Float32Array(0);
  let samplesOut = 0;
  const flushFrom = (all: Float32Array, final: boolean) => {
    let off = 0;
    while (all.length - off >= FRAME_SAMPLES || (final && all.length - off > 0)) {
      const n = Math.min(FRAME_SAMPLES, all.length - off);
      emit(all.slice(off, off + n), startEpochMs + (samplesOut * 1000) / SAMPLE_RATE);
      samplesOut += n;
      off += n;
    }
    pending = all.slice(off);
  };
  return {
    push(samples: Float32Array) {
      const all = new Float32Array(pending.length + samples.length);
      all.set(pending); all.set(samples, pending.length);
      flushFrom(all, false);
    },
    flush() { flushFrom(pending, true); },
    get samplesOut() { return samplesOut; },
  };
}

/** "1:05" for 65 seconds. */
export function clock(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function rms(samples: Float32Array): number {
  if (!samples.length) return 0;
  let s = 0;
  for (let i = 0; i < samples.length; i++) s += samples[i] * samples[i];
  return Math.min(1, Math.sqrt(s / samples.length) * 4);
}

/** What the recorder needs from the browser. */
export interface VoiceNoteDeps {
  fetch: typeof fetch;
  openSocket: (path: string) => WebSocket;
  /** Starts capture; calls `onChunk` with mono Float32 PCM at `sampleRate`; returns a stopper. */
  openMicrophone: (onChunk: (pcm: Float32Array) => void) => Promise<{ sampleRate: number; stop: () => void }>;
  now: () => number;
}

let state: VoiceNoteState = IDLE;
const listeners = new Set<() => void>();
function set(patch: Partial<VoiceNoteState>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}
export function voiceNoteState(): VoiceNoteState { return state; }
export function subscribeVoiceNote(l: () => void): () => void { listeners.add(l); return () => { listeners.delete(l); }; }
export function useVoiceNote(): VoiceNoteState {
  return useSyncExternalStore(subscribeVoiceNote, voiceNoteState, () => IDLE);
}
/** For tests: forget the recorder. */
export function resetVoiceNote() { active?.teardown(); active = null; state = IDLE; listeners.forEach((l) => l()); }

interface Active { nativeId?: string; stop: () => Promise<void>; pause: (p: boolean) => void; teardown: () => void }
let active: Active | null = null;

/** Why a start or a stream failed, in words a person can act on. */
function describe(error: string): string {
  switch (error) {
    case "max_duration": return "The note reached its maximum length and was saved.";
    case "not_found": return "This voice note has already ended.";
    case "insufficient_scope": return "Your key cannot record voice notes (it needs the bot scope).";
    case "auth_unavailable": return "Sign-in could not be checked just now. Try again.";
    case "upstream_unavailable": return "The meetings service is not reachable. Try again.";
    case "microphone": return "Microphone access was refused. Allow it in the browser and try again.";
    case "missing_api_key":
    case "invalid_api_key": return "Your sign-in was not accepted for recording. Sign out, sign in again, and retry.";
    case "ready_timeout": return "Could not reach the recorder. Nothing was saved; try again.";
    default: return error;
  }
}

/** Start a voice note: make the meeting, open the microphone and the socket, stream until Stop.
 *  Resolves with the meeting once audio is flowing (or rejects; the state carries the reason). */
export async function startVoiceNote(deps: VoiceNoteDeps, opts: { speakerName?: string } = {}): Promise<{ id: string; native_meeting_id: string }> {
  if (active) throw new Error("a voice note is already recording");
  set({ ...IDLE, phase: "starting" });
  let mic: { sampleRate: number; stop: () => void } | null = null;
  let ws: WebSocket | null = null;
  let onFail: () => void = () => {};
  const fail = (error: string) => {
    onFail();
    clearTimers();
    mic?.stop(); mic = null;
    try { ws?.close(); } catch { /* already closed */ }
    active = null;
    set({ phase: "error", error: describe(error), level: 0 });
  };
  // THE WATCHDOGS: a socket that never says ready, and a microphone that delivers nothing, are each
  // named within seconds — never a silent "live · 0 lines".
  let readyTimer: ReturnType<typeof setTimeout> | null = null;
  let quietTimer: ReturnType<typeof setInterval> | null = null;
  let quietMs = 0;
  const clearTimers = () => {
    if (readyTimer) clearTimeout(readyTimer); readyTimer = null;
    if (quietTimer) clearInterval(quietTimer); quietTimer = null;
  };
  const queue: ArrayBuffer[] = [];
  let open = false, paused = false, stopping = false;
  let sentSamples = 0;
  let stopped: (() => void) | null = null;
  const send = (pcm: Float32Array, tsMs: number) => {
    const frame = encodeAudioFrame(0, tsMs, pcm);
    sentSamples += pcm.length;
    if (open && ws && ws.readyState === 1) ws.send(frame); else queue.push(frame);
    set({ sentSec: sentSamples / SAMPLE_RATE });
  };
  // A keepalive every 15 s: a paused note sends no audio, and the bot ends a note that goes quiet.
  let keepalive: ReturnType<typeof setInterval> | null = null;
  const stopKeepalive = () => { if (keepalive) clearInterval(keepalive); keepalive = null; };
  onFail = stopKeepalive;
  let framer: ReturnType<typeof createFramer> | null = null;
  let resample: ((x: Float32Array) => Float32Array) | null = null;

  // THE MICROPHONE FIRST, inside the press that started this: a phone's browser starts audio only
  // from a user gesture, and a refused microphone then makes no meeting at all. What is said while
  // the meeting is being made is queued and sent once the socket is ready.
  try {
    mic = await deps.openMicrophone((chunk) => {
      if (paused || stopping || !resample || !framer) return;
      quietMs = 0;
      set({ level: rms(chunk), ...(state.warning ? { warning: undefined } : {}) });
      framer.push(resample(chunk));
    });
  } catch {
    fail("microphone");
    throw new Error(describe("microphone"));
  }
  resample = createResampler(mic.sampleRate);
  framer = createFramer(deps.now(), send);

  const r = await deps.fetch("/api/bots", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ platform: "voice_note", ...(opts.speakerName ? { speaker_name: opts.speakerName } : {}) }),
  });
  if (!r.ok) {
    let detail = `Could not start a voice note (${r.status}).`;
    try { const b = await r.json(); if (typeof b?.detail === "string") detail = b.detail; else if (b?.detail?.message) detail = b.detail.message; } catch { /* keep the status line */ }
    fail(detail);
    throw new Error(detail);
  }
  const meeting = await r.json() as { id: number | string; native_meeting_id: string };
  const meetingId = String(meeting.id), nativeId = meeting.native_meeting_id;
  set({ meetingId, nativeId });

  ws = deps.openSocket(`/voice-notes/${encodeURIComponent(nativeId)}/stream`);
  readyTimer = setTimeout(() => { if (!open) fail("ready_timeout"); }, READY_TIMEOUT_MS);
  quietTimer = setInterval(() => {
    if (paused || stopping) { quietMs = 0; return; }
    quietMs += 1_000;
    if (quietMs >= NO_AUDIO_WARN_MS && !state.warning) set({ warning: NO_AUDIO_WARNING, level: 0 });
  }, 1_000);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (ev) => {
    if (typeof ev.data !== "string") return;
    let msg: { type?: string; received_sec?: number; max_duration_sec?: number; error?: string };
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "ready") {
      open = true;
      if (readyTimer) clearTimeout(readyTimer); readyTimer = null;
      set({ phase: stopping ? "stopping" : "recording", maxSec: msg.max_duration_sec, receivedSec: msg.received_sec ?? 0 });
      while (queue.length && ws && ws.readyState === 1) ws.send(queue.shift() as ArrayBuffer);
      if (!keepalive) keepalive = setInterval(() => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "ping" })); }, 15_000);
      if (stopping) ws?.send(JSON.stringify({ type: "stop" }));
    } else if (msg.type === "progress") {
      set({ receivedSec: msg.received_sec ?? state.receivedSec });
    } else if (msg.type === "stopped") {
      set({ receivedSec: msg.received_sec ?? state.receivedSec });
      stopped?.();
    } else if (msg.type === "error" && msg.error) {
      if (msg.error === "max_duration") { clearTimers(); mic?.stop(); active = null; set({ phase: "stopped", level: 0, error: describe(msg.error) }); stopped?.(); }
      else fail(msg.error);
    }
  };
  ws.onclose = () => {
    stopKeepalive();
    clearTimers();
    if (state.phase === "recording" || state.phase === "starting") fail("The connection to the recorder closed. What was sent is kept.");
    stopped?.();
  };

  active = {
    pause(p: boolean) { paused = p; set({ paused: p, level: 0 }); },
    nativeId,
    teardown() { stopKeepalive(); clearTimers(); mic?.stop(); mic = null; try { ws?.close(); } catch { /* */ } },
    async stop() {
      if (stopping) return;
      stopping = true;
      stopKeepalive();
      clearTimers();
      set({ phase: "stopping", level: 0 });
      mic?.stop();
      framer?.flush();
      const done = new Promise<void>((res) => { stopped = res; });
      if (open && ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "stop" }));
      // A socket that never opened sends Stop on `ready`; one that never answers is not waited on forever.
      await Promise.race([done, new Promise<void>((res) => setTimeout(res, 10_000))]);
      try { ws?.close(); } catch { /* */ }
      active = null;
      if (state.phase !== "error") set({ phase: "stopped" });
    },
  };
  return { id: meetingId, native_meeting_id: nativeId };
}

/** Stop the recording in this tab: the rest of the audio goes, then the end marker. */
export async function stopVoiceNote(): Promise<void> { await active?.stop(); }
/** The note `nativeId` ended on the server (its bot left: Stop elsewhere, the idle rule, the cap).
 *  A recorder still holding it lets the microphone go and leaves the recording state. */
export function endVoiceNote(nativeId: string): void {
  if (!active || active.nativeId !== nativeId) return;
  active.teardown();
  active = null;
  set({ phase: "stopped", level: 0, warning: undefined });
}
/** The person read why a note did not start: back to idle (a recording is never cleared this way). */
export function clearVoiceNoteError(): void {
  if (state.phase === "error" && !active) { state = IDLE; listeners.forEach((l) => l()); }
}
/** Pause or resume capture without ending the note (no audio is sent while paused). */
export function pauseVoiceNote(paused: boolean): void { active?.pause(paused); }

/** The browser's own pieces, for the live app. */
export function browserVoiceNoteDeps(): VoiceNoteDeps {
  return {
    fetch: (...a) => fetch(...a),
    now: () => Date.now(),
    openSocket: (path) => new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`),
    async openMicrophone(onChunk) {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const ctx = new AudioContext();
      if (ctx.state === "suspended") await ctx.resume().catch(() => {});
      const src = ctx.createMediaStreamSource(stream);
      // The worklet hands the main thread each 128-sample render quantum, batched to ~43 ms.
      const code = `class P extends AudioWorkletProcessor{constructor(){super();this.b=[];this.n=0}
        process(i){const c=i[0]&&i[0][0];if(c){this.b.push(new Float32Array(c));this.n+=c.length;
        if(this.n>=2048){const o=new Float32Array(this.n);let k=0;for(const x of this.b){o.set(x,k);k+=x.length}
        this.port.postMessage(o,[o.buffer]);this.b=[];this.n=0}}return true}}registerProcessor('vexa-voice-note',P)`;
      const url = URL.createObjectURL(new Blob([code], { type: "application/javascript" }));
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      const node = new AudioWorkletNode(ctx, "vexa-voice-note", { numberOfInputs: 1, numberOfOutputs: 0, channelCount: 1 });
      node.port.onmessage = (e) => onChunk(e.data as Float32Array);
      src.connect(node);
      return {
        sampleRate: ctx.sampleRate,
        stop: () => {
          try { src.disconnect(); node.disconnect(); } catch { /* */ }
          stream.getTracks().forEach((t) => t.stop());
          void ctx.close().catch(() => {});
        },
      };
    },
  };
}

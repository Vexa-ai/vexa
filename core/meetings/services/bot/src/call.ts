/**
 * A Vexa call — the bot as a hidden listener in a LiveKit room (#1783, ADR-0048 decision 6).
 *
 * A call is a meeting with no page to join: people talk in a self-hosted LiveKit room, and the bot
 * joins that room hidden and subscribe-only (invocation.v1 `call`). Every participant publishes
 * their own audio track, so attribution comes from the track, not from guessing:
 *
 *   • the SOURCE gives each participant identity its own channel, for the whole call, and feeds
 *     that participant's audio (mono 16 kHz, Int16 from the room → Float32) with the participant's
 *     display name as the channel's name; the sound of a screen they share is one more channel,
 *     named "<person> (shared screen)";
 *   • the JOIN DRIVER connects to the room and is admitted at once — nobody admits a listener;
 *   • the call ENDS when the room empties: the last participant pressed Leave (a few seconds), or the
 *     last connection dropped and nobody came back within the grace window, or nobody came at all
 *     within the first-join window. The orchestrator's own
 *     left_alone path finishes the meeting like any meeting.
 *
 * Everything else — the per-channel lane, STT, the transcript and lifecycle sinks — is the meeting
 * bot's own, unchanged. Pure: the room is a port (`CallRoom`); the live adapter is
 * `adapters/livekit-room.ts` over @livekit/rtc-node.
 */
import type { Invocation } from './config.js';
import type { JoinDriver, Pipeline } from './ports.js';

/** One participant as the room reports them. */
export interface CallParticipant {
  /** Stable for the call (the token's `sub`, minted by meeting-api). */
  identity: string;
  /** What the transcript calls them. */
  name?: string;
  /** How they left: pressed Leave (`leave`), or the connection was lost (`lost`). Leaves only. */
  left?: 'leave' | 'lost';
  /** Which of their tracks the audio is from (audio frames only): their microphone (the default), or
   *  the sound of a screen they share (a tab or a video with audio). A shared screen is its own
   *  channel, named "<person> (shared screen)", so its words never interleave with the person's own. */
  source?: 'microphone' | 'screen_share_audio';
}

/** What the transcript appends to a person's name for the sound of the screen they share. */
export const SHARED_SCREEN_SUFFIX = ' (shared screen)';

/** One frame of a participant's audio: interleaved Int16 at `sampleRate`, `channels` channels. */
export interface CallAudioFrame {
  data: Int16Array;
  sampleRate: number;
  channels: number;
}

export interface CallRoomHandlers {
  participantJoined(p: CallParticipant): void;
  participantLeft(p: CallParticipant): void;
  /** One frame of a participant's microphone track, or of the audio of a screen they share. */
  audio(p: CallParticipant, frame: CallAudioFrame): void;
  /** The room is gone (deleted, or the connection is lost for good). */
  disconnected(reason: string): void;
}

export interface CallRoom {
  /** Join hidden; the adapter reports participants already in the room as joins. */
  connect(handlers: CallRoomHandlers): Promise<void>;
  disconnect(): Promise<void>;
}

export interface CallSourceOptions {
  room: CallRoom;
  /** One decoded frame for the lane: its channel, the speaker's name, mono 16 kHz PCM, capture ms. */
  feed: (channel: number, name: string, pcm: Float32Array, tsMs: number) => void;
  /** The room emptied (or never filled). Fires once. */
  onEmpty: (why: 'everyone_left' | 'nobody_joined' | 'room_closed') => void;
  /** How long an empty room is given for someone to come back. */
  emptyGraceMs: number;
  /** How long the listener waits for the first participant. */
  firstJoinTimeoutMs: number;
  log?: (m: string) => void;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (t: unknown) => void;
}

export interface SpeakerStats { channel: number; name: string; frames: number; audioSeconds: number; silentFrames: number }

export interface CallSource {
  start(): Promise<void>;
  stop(): Promise<void>;
  stats(): { participants: number; speakers: Record<string, SpeakerStats>; ended: boolean };
}

const TARGET_RATE = 16_000;
/** THE CALL ENDS WITH ITS PEOPLE (founder 2026-10-15: "it should just start and stop together with
 *  the call"). When the last person in the room pressed Leave, the listener waits only this long — a
 *  misclick's worth — before ending the call; a dropped connection keeps the full grace window to
 *  come back. Nobody ever stops a call's transcriber by hand. */
export const LEAVE_GRACE_MS = 3000;
/** A frame whose every sample is within this of zero is digital silence: what the room delivers for
 *  a muted or DTX-silent participant. It is not fed, so the lane sees the gap a page capture shows
 *  between speech instead of an endless stretch of zeros that only its hard cap would cut. */
export const DIGITAL_SILENCE_PEAK = 1;

export function isDigitalSilence(frame: CallAudioFrame): boolean {
  const d = frame.data;
  for (let i = 0; i < d.length; i++) if (d[i] > DIGITAL_SILENCE_PEAK || d[i] < -DIGITAL_SILENCE_PEAK) return false;
  return true;
}

/** Int16 interleaved at any rate → Float32 mono at 16 kHz (channel average, linear resample). */
export function toMono16k(frame: CallAudioFrame): Float32Array {
  const ch = Math.max(1, frame.channels);
  const n = Math.floor(frame.data.length / ch);
  const mono = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let c = 0; c < ch; c++) s += frame.data[i * ch + c];
    mono[i] = s / ch / 32768;
  }
  if (frame.sampleRate === TARGET_RATE || n === 0) return mono;
  const outN = Math.max(1, Math.round((n * TARGET_RATE) / frame.sampleRate));
  const out = new Float32Array(outN);
  const step = n / outN;
  for (let i = 0; i < outN; i++) {
    const x = i * step;
    const i0 = Math.floor(x);
    const i1 = Math.min(n - 1, i0 + 1);
    const f = x - i0;
    out[i] = mono[i0] * (1 - f) + mono[i1] * f;
  }
  return out;
}

export function createCallSource(opts: CallSourceOptions): CallSource {
  const now = opts.now ?? Date.now;
  const setTimer = opts.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms));
  const clearTimer = opts.clearTimer ?? ((t: unknown) => clearTimeout(t as ReturnType<typeof setTimeout>));
  const log = opts.log ?? (() => { /* quiet */ });
  const channels = new Map<string, SpeakerStats>();
  const present = new Set<string>();
  let everJoined = false;
  let ended = false;
  let stopped = false;
  let timer: unknown = null;

  const end = (why: 'everyone_left' | 'nobody_joined' | 'room_closed') => {
    if (ended || stopped) return;
    ended = true;
    log(`[bot] call: ending (${why})`);
    opts.onEmpty(why);
  };
  const arm = (ms: number, why: 'everyone_left' | 'nobody_joined') => {
    if (timer !== null) clearTimer(timer);
    timer = setTimer(() => { timer = null; if (present.size === 0) end(why); }, ms);
  };
  const disarm = () => { if (timer !== null) { clearTimer(timer); timer = null; } };

  // One channel per participant microphone, and one more per participant's shared-screen audio.
  const channelOf = (p: CallParticipant): SpeakerStats => {
    const screen = p.source === 'screen_share_audio';
    const key = screen ? `${p.identity}#screen` : p.identity;
    const label = (n: string) => (screen ? `${n}${SHARED_SCREEN_SUFFIX}` : n);
    let s = channels.get(key);
    if (!s) {
      s = { channel: channels.size, name: label(p.name || p.identity), frames: 0, audioSeconds: 0, silentFrames: 0 };
      channels.set(key, s);
      log(`[bot] call: ${s.name} on channel ${s.channel}`);
    } else if (p.name && label(p.name) !== s.name) {
      s.name = label(p.name);
    }
    return s;
  };

  const handlers: CallRoomHandlers = {
    participantJoined(p) {
      if (ended) return;
      present.add(p.identity);
      everJoined = true;
      channelOf(p);
      disarm();
    },
    participantLeft(p) {
      present.delete(p.identity);
      if (present.size === 0 && !ended) arm(p.left === 'leave' ? Math.min(LEAVE_GRACE_MS, opts.emptyGraceMs) : opts.emptyGraceMs, 'everyone_left');
    },
    audio(p, frame) {
      if (ended || stopped) return;
      const s = channelOf(p);
      if (isDigitalSilence(frame)) { s.silentFrames++; return; }
      const pcm = toMono16k(frame);
      if (pcm.length === 0) return;
      s.frames++;
      s.audioSeconds += pcm.length / TARGET_RATE;
      // The listener stamps each frame as it arrives: the room delivers in real time.
      opts.feed(s.channel, s.name, pcm, now());
    },
    disconnected(reason) {
      log(`[bot] call: room disconnected (${reason})`);
      end('room_closed');
    },
  };

  return {
    async start() {
      await opts.room.connect(handlers);
      if (!everJoined) arm(opts.firstJoinTimeoutMs, 'nobody_joined');
    },
    async stop() {
      stopped = true;
      disarm();
      await opts.room.disconnect().catch(() => { /* best-effort */ });
    },
    stats() {
      return { participants: present.size, speakers: Object.fromEntries(channels), ended };
    },
  };
}

/** Nobody admits a hidden listener: the call is active the moment the room is joined (the pipeline's
 *  start does the joining). */
export function createCallJoinDriver(): JoinDriver {
  return {
    async join() { return 'admitted'; },
    onRemoval() { return () => { /* the room's end arrives through the source */ }; },
    async leave() { /* the pipeline's stop leaves the room */ },
    async withdraw() { /* nothing to withdraw */ },
  };
}

/** The orchestrator's Pipeline over a call: the lane first, then the room; on stop the room is left
 *  before the lane flushes its last turn. */
export function createCallPipeline(deps: { source: Pick<CallSource, 'start' | 'stop'>; engine: Pipeline }): Pipeline {
  return {
    async start() {
      await deps.engine.start();
      await deps.source.start();
    },
    async stop() {
      await deps.source.stop().catch(() => { /* best-effort */ });
      await deps.engine.stop().catch(() => { /* best-effort */ });
    },
  };
}

/** The active-phase backstop: the call's cap. */
export function callMaxActiveMs(inv: Invocation): number {
  return (inv.call?.maxDurationSec ?? 4 * 3600) * 1000;
}

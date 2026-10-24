/**
 * The demo presenter — the scripted speakers of a demo call (invocation.v1 `call.presenter`).
 *
 * A demo call is a room nobody hosts: a person who has no meeting right now asks for a demo
 * (`POST /demo-meetings`), and their own listener joins a fresh room as on any call. So that the room
 * has something to transcribe, the same bot also joins it once per scripted speaker — each with the
 * publish token meeting-api minted for that speaker — and plays the script bundled in the image
 * (`demo/<script>/`: `script.json` plus one 16 kHz mono WAV per turn), one turn after another.
 * The listener hears the speakers as ordinary participants, each on their own channel under their
 * own name, so the demo exercises the real path: room → lane → STT → transcript.
 *
 * When the last turn has played, every speaker leaves; the room is then empty and the call ends the
 * way every call ends (`call.ts`, `everyone_left`). A presenter that cannot start (a missing script,
 * a token the room refuses) leaves the room empty, so the call ends by its first-join window: the
 * failure is logged, never thrown into the orchestrator.
 *
 * Pure over an injected SDK slice and file reader; the live composition root passes
 * `@livekit/rtc-node` and `node:fs`.
 */
import type { CallRoom, CallRoomHandlers } from './call.js';

/** invocation.v1 `call.presenter`. */
export interface Presenter {
  script: string;
  speakers: { name: string; token: string }[];
}

export interface ScriptTurn { speaker: string; audio: string; text?: string }
export interface DemoScript {
  script: string;
  sampleRate: number;
  /** Silence before the first turn, once every speaker is in the room. */
  leadInMs?: number;
  /** Silence between turns. */
  gapMs?: number;
  speakers: string[];
  turns: ScriptTurn[];
}

/** The slice of @livekit/rtc-node a presenter uses. */
export interface PresenterSdk {
  Room: new () => {
    connect(url: string, token: string, opts?: { autoSubscribe?: boolean; dynacast?: boolean }): Promise<void>;
    disconnect(): Promise<void>;
    localParticipant?: { publishTrack(track: unknown, opts: unknown): Promise<unknown> } | null;
  };
  AudioSource: new (sampleRate: number, channels: number) => {
    captureFrame(frame: unknown): Promise<void>;
    waitForPlayout?: () => Promise<void>;
  };
  AudioFrame: new (data: Int16Array, sampleRate: number, channels: number, samplesPerChannel: number) => unknown;
  LocalAudioTrack: { createAudioTrack(name: string, source: unknown): unknown };
  TrackPublishOptions: new () => { source?: unknown };
  TrackSource: { SOURCE_MICROPHONE: unknown };
}

/** 10 ms at 16 kHz: the frame the room is fed in. */
const FRAME = 160;

/** The PCM of a 16-bit mono WAV (the `data` chunk). A header whose data size is unknown (streamed,
 *  0xFFFFFFFF) is read to the end of the file. */
export function wavPcm(buf: Uint8Array): { pcm: Int16Array; sampleRate: number } {
  const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const tag = (o: number) => String.fromCharCode(buf[o], buf[o + 1], buf[o + 2], buf[o + 3]);
  if (buf.byteLength < 12 || tag(0) !== 'RIFF' || tag(8) !== 'WAVE') throw new Error('not a WAV file');
  let sampleRate = 0, channels = 0, bits = 0;
  let off = 12;
  while (off + 8 <= buf.byteLength) {
    const id = tag(off);
    let size = view.getUint32(off + 4, true);
    if (id === 'fmt ') {
      channels = view.getUint16(off + 10, true);
      sampleRate = view.getUint32(off + 12, true);
      bits = view.getUint16(off + 22, true);
    } else if (id === 'data') {
      if (channels !== 1 || bits !== 16) throw new Error(`WAV must be 16-bit mono (got ${bits}-bit, ${channels} channel(s))`);
      if (size === 0xffffffff || off + 8 + size > buf.byteLength) size = buf.byteLength - off - 8;
      const bytes = buf.slice(off + 8, off + 8 + (size & ~1));
      return { pcm: new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2), sampleRate };
    }
    off += 8 + size + (size % 2);
  }
  throw new Error('WAV has no data chunk');
}

/** Load a bundled script and check it against the invocation: every voice the script speaks in has a
 *  token, and every audio file is 16-bit mono at the script's rate. */
export function loadScript(
  presenter: Presenter,
  read: (relPath: string) => Uint8Array,
): { script: DemoScript; turns: { speaker: string; pcm: Int16Array }[] } {
  const script = JSON.parse(new TextDecoder().decode(read('script.json'))) as DemoScript;
  if (script.script !== presenter.script) throw new Error(`script.json names '${script.script}', not '${presenter.script}'`);
  const tokens = new Set(presenter.speakers.map((s) => s.name));
  const turns = script.turns.map((t) => {
    if (!tokens.has(t.speaker)) throw new Error(`no token for the script's speaker '${t.speaker}'`);
    const { pcm, sampleRate } = wavPcm(read(t.audio));
    if (sampleRate !== script.sampleRate) throw new Error(`${t.audio} is ${sampleRate} Hz, the script says ${script.sampleRate}`);
    return { speaker: t.speaker, pcm };
  });
  if (!turns.length) throw new Error('the script has no turns');
  return { script, turns };
}

export interface PresenterRun {
  /** Resolves when the script has played and every speaker has left (or the run was stopped). */
  done: Promise<{ turns: number; secondsPublished: number }>;
  /** Stop mid-script: every speaker leaves now. */
  stop(): Promise<void>;
}

export function runPresenter(opts: {
  sdk: PresenterSdk;
  url: string;
  presenter: Presenter;
  script: DemoScript;
  turns: { speaker: string; pcm: Int16Array }[];
  log?: (m: string) => void;
  sleep?: (ms: number) => Promise<void>;
}): PresenterRun {
  const { sdk, url, presenter, script, turns } = opts;
  const log = opts.log ?? (() => { /* quiet */ });
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const rate = script.sampleRate;
  const rooms: { disconnect(): Promise<void> }[] = [];
  let stopped = false;
  const leave = async () => {
    const all = rooms.splice(0);
    await Promise.all(all.map((r) => r.disconnect().catch(() => { /* already gone */ })));
  };

  const done = (async () => {
    let published = 0;
    let played = 0;
    try {
      const voices = new Map<string, InstanceType<PresenterSdk['AudioSource']>>();
      for (const s of presenter.speakers) {
        if (stopped) break;
        const room = new sdk.Room();
        rooms.push(room);
        await room.connect(url, s.token, { autoSubscribe: false, dynacast: false });
        const source = new sdk.AudioSource(rate, 1);
        const track = sdk.LocalAudioTrack.createAudioTrack('microphone', source);
        const options = new sdk.TrackPublishOptions();
        options.source = sdk.TrackSource.SOURCE_MICROPHONE;
        await room.localParticipant?.publishTrack(track, options);
        voices.set(s.name, source);
      }
      log(`[presenter] ${voices.size} speaker(s) in the room; script ${script.script}, ${turns.length} turn(s)`);
      await sleep(script.leadInMs ?? 1500);
      for (const t of turns) {
        if (stopped) break;
        const source = voices.get(t.speaker);
        if (!source) continue;
        const step = Math.round((rate * FRAME) / 16_000);
        for (let i = 0; i + step <= t.pcm.length && !stopped; i += step) {
          await source.captureFrame(new sdk.AudioFrame(t.pcm.slice(i, i + step), rate, 1, step));
          published += step;
        }
        await source.waitForPlayout?.();
        played++;
        await sleep(script.gapMs ?? 600);
      }
      log(`[presenter] script played: ${played} turn(s), ${(published / rate).toFixed(1)}s of speech`);
    } catch (e) {
      log(`[presenter] stopped on an error: ${(e as Error).message}`);
    } finally {
      await leave();
    }
    return { turns: played, secondsPublished: +(published / rate).toFixed(2) };
  })();

  return {
    done,
    async stop() { stopped = true; await leave(); },
  };
}

/** The listener's room, with the presenter started once the listener is in it — so not a word is
 *  spoken before anyone is listening — and stopped when the listener leaves. */
export function withPresenter(room: CallRoom, start: () => PresenterRun | null): CallRoom {
  let run: PresenterRun | null = null;
  return {
    async connect(handlers: CallRoomHandlers) {
      await room.connect(handlers);
      run = start();
    },
    async disconnect() {
      await run?.stop();
      await room.disconnect();
    },
  };
}

/**
 * createMixedLaneCapture — the MIXED lane's page-side capture (Teams, Jitsi) as ONE module the bot and the
 * capture extension both run (founder 9 Nov 2026: «must support gmeet and msteams as well», «no shortcuts»;
 * card brain:card/66d7eaa642e5).
 *
 * Moved in behaviour from the bot's capture-bridge.ts page closure (setupMix + reportStreamPresence):
 *   - every rescan reports how many hooked remote streams are LIVE (a stream object walk, never the
 *     monotonic id ledger: a bot that once saw a participant must still be able to read an empty room);
 *   - on Teams, selectTeamsMixStreams picks the server-side mainAudio mix and FAILS OPEN to every track,
 *     and mainAudioProvedSilent latches a picked mix that carried no sound (observed as main-audio-absent);
 *   - the picked streams are summed into one 16 kHz MediaStreamDestination, a late stream joins on rescan,
 *     the topology is observed on every change;
 *   - createMixedAudioCapture taps the sum; each frame is metered (frames, energetic ms) and handed on with
 *     the capture's own accumulated-audio clock (tsMs), never receipt time;
 *   - the silence clock starts when capture STARTS, so a pick that delivers nothing still arms the fallback.
 * Pure browser code; the host injects the I/O. installRemoteAudioHook must run at document_start before it.
 */
import { createMixedAudioCapture } from './mixed-audio.js';
import type { MixedAudioCapture } from './mixed-audio.js';
import {
  selectTeamsMixStreams, mainAudioProvedSilent,
  TEAMS_MAIN_AUDIO_GRACE_MS, TEAMS_MAIN_AUDIO_SILENCE_MS, TEAMS_MAIN_AUDIO_ENERGY_RMS,
} from './teams-main-audio.js';

export interface MixedLaneCaptureOptions {
  /** The remote audio streams the WebRTC hook has mirrored so far (read on every rescan). */
  streams: () => MediaStream[];
  /** One captured frame of the summed lane: 16 kHz PCM and its capture epoch ms. */
  onAudio: (pcm: Float32Array, tsMs: number | undefined) => void;
  /** Teams: mix only the server-side mainAudio track (fail open to every track). */
  teamsMainAudio?: boolean;
  /** The platform label on observations ('teams' | 'mixed'). */
  platform?: string;
  graceMs?: number;
  silenceMs?: number;
  energyRms?: number;
  log?: (msg: string) => void;
  observe?: (kind: string, data: Record<string, unknown>, tMs: number) => void;
  onPresence?: (live: number) => void;
  /** The capture is running (awaited before the start is logged, as the bot did). */
  onReady?: () => unknown;
  rescanMs?: number;
  /** Test seams. */
  createAudioContext?: () => AudioContext;
  createMixed?: typeof createMixedAudioCapture;
  select?: typeof selectTeamsMixStreams;
  provedSilent?: typeof mainAudioProvedSilent;
  setInterval?: (fn: () => void, ms: number) => unknown;
  clearInterval?: (h: unknown) => void;
  now?: () => number;
}

export interface MixedLaneCapture {
  rescan(): void;
  /** Streams summed into the lane so far. */
  channels(): number;
  contextState(): string;
  /** Stop only the capture tap; the next rescan re-creates it (the bot's deaf-capture guard). */
  restart(): void;
  /** Meter readings, for diagnostics and tests. */
  meter(): { frames: number; energeticMs: number; captureStartedMs: number | null; mainAudioDead: boolean };
  rescanHandle: unknown;
  stop(): void;
}

export function createMixedLaneCapture(opts: MixedLaneCaptureOptions): MixedLaneCapture {
  const log = opts.log ?? (() => { /* silent */ });
  const observe = opts.observe ?? (() => { /* silent */ });
  const now = opts.now ?? (() => Date.now());
  const setI = opts.setInterval ?? ((fn: () => void, ms: number) => setInterval(fn, ms));
  const clearI = opts.clearInterval ?? ((h: unknown) => clearInterval(h as ReturnType<typeof setInterval>));
  const makeMixed = opts.createMixed ?? createMixedAudioCapture;
  const select = opts.select ?? selectTeamsMixStreams;
  const provedSilent = opts.provedSilent ?? mainAudioProvedSilent;
  const graceMs = opts.graceMs ?? TEAMS_MAIN_AUDIO_GRACE_MS;
  const silenceMs = opts.silenceMs ?? TEAMS_MAIN_AUDIO_SILENCE_MS;
  const energyRms = opts.energyRms ?? TEAMS_MAIN_AUDIO_ENERGY_RMS;
  const platform = opts.platform ?? (opts.teamsMainAudio ? 'teams' : 'mixed');

  let ctx: AudioContext | null = null;
  let dest: MediaStreamAudioDestinationNode | null = null;
  const seen = new Set<string>();
  const refs: MediaStream[] = [];
  let capture: MixedAudioCapture | true | null = null;   // `true` guards re-entry while the async create resolves
  let gen = 0;                                             // bumps on restart/stop: a stale create is dropped
  let frames = 0, energeticMs = 0;
  let captureStartedMs: number | null = null;
  let mainAudioDead = false;
  let noMainSince: number | null = null;
  let topology = -1;
  let stopped = false;

  const reportPresence = (): void => {
    let live = 0;
    for (const s of refs) {
      try {
        for (const t of s?.getAudioTracks ? s.getAudioTracks() : []) {
          if (t && t.readyState === 'live' && t.muted !== true) { live++; break; }
        }
      } catch { /* a stream may have been torn down mid-walk */ }
    }
    try { opts.onPresence?.(live); } catch { /* presence must never break capture */ }
  };
  const reportTopology = (): void => {
    if (topology === seen.size) return;
    topology = seen.size;
    observe('mixed', { type: 'mix-topology', streams: seen.size, tMs: now() }, now());
  };

  const rescan = (): void => {
    if (stopped) return;
    reportPresence();
    let streams = (opts.streams() || []) as MediaStream[];
    if (opts.teamsMainAudio && streams.length) {
      if (!mainAudioDead && provedSilent({ captureStartedMs, energeticMs, nowMs: now(), silenceMs })) {
        mainAudioDead = true;
        log('[mixed] MAIN-AUDIO ABSENT — the picked mix produced no sound; falling back to every track');
        observe('mixed', {
          type: 'main-audio-absent', platform,
          capturedMs: captureStartedMs ? now() - captureStartedMs : 0,
          energeticMs, framesSeen: frames, silenceMs, tMs: now(),
        }, now());
      }
      const sel = select(streams as any, {
        firstMissMs: noMainSince, nowMs: now(), graceMs,
        mainAudioSilent: mainAudioDead,
        mainAudioCapturedMs: captureStartedMs ? now() - captureStartedMs : 0,
      });
      if (sel.outcome === 'main-audio') noMainSince = null;
      else noMainSince = noMainSince || now();
      if (sel.observation) {
        log('[mixed] observation ' + JSON.stringify(sel.observation));
        observe('mixed', sel.observation as unknown as Record<string, unknown>, now());
      }
      streams = sel.streams as unknown as MediaStream[];
    }
    if (!streams.length) return;
    if (!ctx) {
      ctx = opts.createAudioContext ? opts.createAudioContext() : new AudioContext({ sampleRate: 16000 });
      try { void Promise.resolve((ctx as any).resume?.()).catch(() => { /* reported by state */ }); } catch { /* */ }
      dest = ctx.createMediaStreamDestination();
    }
    for (const s of streams) {
      if (!s || seen.has(s.id)) continue;
      try {
        ctx.createMediaStreamSource(s).connect(dest!);
        seen.add(s.id);
        refs.push(s);
        log('[mixed] connected remote stream ' + seen.size);
        reportTopology();
      } catch { /* a stream may not be connectable yet */ }
    }
    if (!capture && seen.size) {
      capture = true;
      const myGen = gen;
      const meterAndForward = (pcm: Float32Array, tsMs?: number): void => {
        if (captureStartedMs === null) captureStartedMs = now();
        frames++;
        let sum = 0;
        for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
        if (pcm.length && Math.sqrt(sum / pcm.length) >= energyRms) energeticMs += (pcm.length / 16000) * 1000;
        if (!stopped) opts.onAudio(pcm, tsMs);
      };
      Promise.resolve(makeMixed(dest!.stream, meterAndForward))
        .then((cap: any) => {
          if (stopped || myGen !== gen) { try { cap?.stop?.(); } catch { /* */ } return; }
          capture = cap;
          if (captureStartedMs === null) captureStartedMs = now();
          return cap?.start?.();
        })
        .then(async () => {
          if (stopped) return;
          await opts.onReady?.();
          log('[mixed] capture started over ' + seen.size + ' stream(s)');
        })
        .catch((e: unknown) => { capture = null; log('[mixed] capture start failed: ' + String(e)); });
    }
  };

  rescan();
  const timer = setI(rescan, opts.rescanMs ?? 2000);   // pick up late-arriving tracks

  const stopTap = (): void => {
    gen++;
    try { if (capture && capture !== true) capture.stop(); } catch { /* best effort */ }
    capture = null;
  };
  return {
    rescan,
    channels: () => seen.size,
    contextState: () => (ctx ? String((ctx as any).state) : 'none'),
    restart: () => { if (!stopped) stopTap(); },
    meter: () => ({ frames, energeticMs, captureStartedMs, mainAudioDead }),
    rescanHandle: timer,
    stop(): void {
      if (stopped) return;
      stopped = true;
      clearI(timer);
      stopTap();
      try { Promise.resolve(ctx?.close()).catch(() => { /* already closed */ }); } catch { /* best effort */ }
      ctx = null; dest = null;
    },
  };
}

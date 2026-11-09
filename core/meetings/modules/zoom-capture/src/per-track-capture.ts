/**
 * createZoomPerTrackCapture — Zoom's per-track capture, as ONE module the bot and the extension both run
 * (founder 9 Nov 2026: «port zoom bot capture to the extension - reuse the code»; card brain:card/66d7eaa642e5).
 *
 * Moved verbatim in behaviour from the bot's capture-bridge.ts page closure (the PER-TRACK branch), so
 * the bot keeps doing exactly what it did and the extension does the same thing:
 *   - each remote WebRTC audio track (mirrored into `streams()` by installRemoteAudioHook, which must run
 *     before the page builds its RTCPeerConnections) gets its own stable channel, tapped at 16 kHz on ONE
 *     shared AudioContext (Chromium caps contexts at 6);
 *   - frames are stamped on one epoch-anchored monotonic page clock; silence is gated (peak ≤ 0.005);
 *   - createTrackNameResolver binds each channel to a display name from createZoomSpeakers' active-speaker
 *     edges (vote · hysteresis · 1:1 · self-exclusion); an UNBOUND channel crosses with NO name, so the
 *     per-channel lane opens the turn unnamed and renames it in place when the resolver binds;
 *   - presence (live, unmuted tracks), topology and faults are reported as data.
 * Pure browser code: no Node, no Playwright, no globals of its own. The host injects the I/O.
 */
import { createTrackNameResolver } from './track-name-resolver.js';
import type { TrackNameResolver } from './track-name-resolver.js';
import { createZoomSpeakers } from './zoom-speakers.js';
import type { ZoomSpeakers } from './zoom-speakers.js';

export interface ZoomPerTrackCaptureOptions {
  /** The remote audio streams the WebRTC hook has mirrored so far (read on every rescan). */
  streams: () => MediaStream[];
  /** One captured frame: its channel, the bound name (or null while unbound), 16 kHz PCM, capture epoch ms. */
  onAudio: (channel: number, name: string | null, samples: Float32Array, tsMs: number) => void;
  /** The local participant (the bot, or the person running the extension): never a remote channel's name. */
  selfName?: string;
  log?: (msg: string) => void;
  /** Typed observations (kind, payload, tMs): binds, flips, topology, gaps, faults. */
  observe?: (kind: string, data: Record<string, unknown>, tMs: number) => void;
  /** Live, unmuted remote tracks, reported on every rescan (including zero). */
  onPresence?: (live: number) => void;
  /** The first track is tapped (audio is flowing to the host). */
  onReady?: () => void;
  /** Active-speaker edges, teed for telemetry (the resolver already consumes them). */
  onSpeakerHint?: (name: string, tMs: number, isEnd: boolean) => void;
  /** Watch Zoom's active-speaker DOM (default true). */
  watchSpeakers?: boolean;
  rescanMs?: number;
  /** Test seams. */
  createAudioContext?: () => AudioContext;
  setInterval?: (fn: () => void, ms: number) => unknown;
  clearInterval?: (h: unknown) => void;
  nowPerf?: () => number;
  nowEpoch?: () => number;
  makeResolver?: typeof createTrackNameResolver;
  makeSpeakers?: typeof createZoomSpeakers;
}

export interface ZoomPerTrackCapture {
  /** Tap any newly mirrored tracks (also runs every `rescanMs`). */
  rescan(): void;
  /** Channels tapped so far. */
  channels(): number;
  /** The resolver (diagnostics: bindings()). */
  resolver: TrackNameResolver;
  /** The active-speaker watcher, when one runs (diagnostics: getState()). */
  speakers: ZoomSpeakers | null;
  /** The shared AudioContext's state ('none' before the first track): a host that cannot autoplay
   *  (the extension in a normal tab) sees 'suspended' here and in a 'pertrack-context' observation. */
  contextState(): string;
  /** The tapped tracks by stream id (diagnostics and wiring tests; read-only by contract). */
  taps: ReadonlyMap<string, { ch: number; proc: ScriptProcessorNode }>;
  /** The rescan interval's handle (a host that tears down by clearInterval can use it). */
  rescanHandle: unknown;
  stop(): void;
}

const SR = 16000;
const SILENCE = 0.005;

export function createZoomPerTrackCapture(opts: ZoomPerTrackCaptureOptions): ZoomPerTrackCapture {
  const log = opts.log ?? (() => { /* silent */ });
  const observe = opts.observe ?? (() => { /* silent */ });
  const nowPerf = opts.nowPerf ?? (() => performance.now());
  const nowEpoch = opts.nowEpoch ?? (() => Date.now());
  const setI = opts.setInterval ?? ((fn: () => void, ms: number) => setInterval(fn, ms));
  const clearI = opts.clearInterval ?? ((h: unknown) => clearInterval(h as ReturnType<typeof setInterval>));
  const makeResolver = opts.makeResolver ?? createTrackNameResolver;
  const makeSpeakers = opts.makeSpeakers ?? createZoomSpeakers;

  const bound = new Map<number, string>();
  const resolver = makeResolver({
    mode: 'exclusive',
    // The leak-proof backstop: our own tile can never become a remote channel's identity.
    selfName: opts.selfName,
    onBind: (ch: number, name: string, votes: number): void => {
      const prev = bound.get(ch);
      bound.set(ch, name);
      log('[pertrack] bound ch=' + ch + ' → ' + name + ' (' + votes + ' votes)');
      observe('pertrack', { type: prev ? 'pertrack-flip' : 'pertrack-bind', channel: ch, name, previous: prev, votes, tMs: nowEpoch() }, nowEpoch());
    },
  });

  let ctx: AudioContext | null = null;
  let epochMs = 0;
  let nextCh = 0;
  let topology = -1;
  let stopped = false;
  const caps = new Map<string, { ch: number; src: MediaStreamAudioSourceNode; proc: ScriptProcessorNode }>();

  const reportPresence = (): void => {
    let live = 0;
    for (const s of opts.streams() || []) {
      try {
        for (const t of s?.getAudioTracks ? s.getAudioTracks() : []) {
          if (t && t.readyState === 'live' && t.muted !== true) { live++; break; }
        }
      } catch { /* a stream may have been torn down mid-walk */ }
    }
    try { opts.onPresence?.(live); } catch { /* presence must never break capture */ }
  };
  const reportTopology = (): void => {
    if (topology === caps.size) return;
    topology = caps.size;
    observe('pertrack', { type: 'pertrack-topology', streams: caps.size, tMs: nowEpoch() }, nowEpoch());
  };

  const rescan = (): void => {
    if (stopped) return;                               // a late call after stop() builds nothing
    reportPresence();
    const streams = opts.streams() || [];
    if (!streams.length) return;
    if (!ctx) {
      ctx = opts.createAudioContext ? opts.createAudioContext() : new AudioContext({ sampleRate: SR });
      const c = ctx as any;
      const reportState = (): void => {
        observe('pertrack', { type: 'pertrack-context', state: String(c.state), tMs: nowEpoch() }, nowEpoch());
        if (c.state !== 'running') log('[pertrack] AudioContext is ' + c.state + ': no frames until it runs');
      };
      try { c.onstatechange = reportState; } catch { /* a context without the event */ }
      Promise.resolve(c.resume?.()).then(reportState, reportState);
      epochMs = nowEpoch() - nowPerf();
    }
    for (const s of streams) {
      if (!s || caps.has(s.id)) continue;
      const ch = nextCh++;
      try {
        const src = ctx.createMediaStreamSource(s);
        const proc = ctx.createScriptProcessor(4096, 1, 1);
        let lastFrameEndMs: number | undefined;
        let lastGapReportMs = -Infinity;
        proc.onaudioprocess = (e: AudioProcessingEvent): void => {
          const input = e.inputBuffer.getChannelData(0);
          const frameMs = (input.length / SR) * 1000;
          const frameEndMs = epochMs + nowPerf();
          const ts = frameEndMs - frameMs;
          // Callback delivery approximates capture time; discontinuities are reported before the silence
          // gate so they stay distinguishable from a participant simply not speaking.
          const gapMs = lastFrameEndMs === undefined ? 0 : ts - lastFrameEndMs;
          if (gapMs > 1000 && frameEndMs - lastGapReportMs >= 10000) {
            observe('pertrack', { type: 'capture-clock-gap', channel: ch, gapMs, frameMs, tMs: ts }, ts);
            lastGapReportMs = frameEndMs;
          }
          lastFrameEndMs = frameEndMs;
          let maxVal = 0;
          for (let i = 0; i < input.length; i++) { const a = Math.abs(input[i]); if (a > maxVal) maxVal = a; }
          if (maxVal <= SILENCE) return;                 // gate silence
          resolver.markHot(ch, ts, maxVal);              // peak energy → the dominant-slot ranking
          const name = resolver.resolve(ch, ts) ?? null;
          opts.onAudio(ch, name, new Float32Array(input), ts);   // a copy: the input buffer is reused
        };
        src.connect(proc);
        proc.connect(ctx.destination);                   // pull the processor (it outputs silence)
        caps.set(s.id, { ch, src, proc });
        log('[pertrack] capturing ch=' + ch + ' (' + caps.size + ' track(s))');
        reportTopology();
        // Signalled on every tapped track, as the bot always did (it re-arms the deaf-capture window).
        try { opts.onReady?.(); } catch { /* never break capture */ }
      } catch (e) {
        log('[pertrack] track setup failed ch=' + ch + ': ' + String(e));
        observe('pertrack', { type: 'pertrack-capture-failed', channel: ch, error: String(e), tMs: nowEpoch() }, nowEpoch());
      }
    }
  };

  let speakers: ZoomSpeakers | null = null;
  if (opts.watchSpeakers !== false) {
    let lastActive: string | null = null;
    speakers = makeSpeakers({
      selfName: opts.selfName,
      log: (m: string) => log('[ZoomSpeakers] ' + m),
      onSpeakerChange: (name: string | null) => {
        const tMs = nowEpoch();
        if (name) { resolver.onSpeak(name, tMs, false); opts.onSpeakerHint?.(name, tMs, false); }
        else if (lastActive) { resolver.onSpeak(lastActive, tMs, true); opts.onSpeakerHint?.(lastActive, tMs, true); }
        lastActive = name;
      },
    });
  }

  rescan();
  const timer = setI(rescan, opts.rescanMs ?? 2000);   // pick up late-joining tracks

  return {
    rescan,
    channels: () => caps.size,
    contextState: () => (ctx ? String((ctx as any).state) : 'none'),
    resolver,
    speakers,
    taps: caps,
    rescanHandle: timer,
    stop(): void {
      if (stopped) return;
      stopped = true;
      clearI(timer);
      for (const c of caps.values()) {
        try { c.proc.onaudioprocess = null; c.src.disconnect(); c.proc.disconnect(); } catch { /* already gone */ }
      }
      caps.clear();
      try { speakers?.destroy(); } catch { /* best effort */ }
      try { Promise.resolve(ctx?.close()).catch(() => { /* already closed */ }); } catch { /* best effort */ }
      ctx = null;
    },
  };
}

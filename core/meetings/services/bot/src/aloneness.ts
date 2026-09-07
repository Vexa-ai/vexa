/** Active-phase aloneness derived from the remote-audio signal. */
import type { AlonenessSource } from './ports.js';

export const DEFAULT_ALONE_SILENCE_WINDOW_MS = 10 * 60 * 1000;
export const DEFAULT_ALONENESS_POLL_MS = 1_500;
/** Presence floor for a DELIVERED remote frame — deliberately 0 (arrival is the signal).
 *
 *  Capture is the single silence oracle: the page emits a frame only when its PEAK sample exceeds
 *  its own gate (`mixed-audio.ts` / `gmeet-capture.ts`, 0.005), and the activity tap sits on the
 *  Node side of that gate (`capture-bridge.ts:289,298`). So every frame that reaches this seam has
 *  ALREADY proven it carries audio — and was sent to STT and transcribed on that basis.
 *
 *  Re-testing such a frame with RMS (always ≤ peak; for speech 3–5× lower) against the SAME 0.005
 *  could only ever REJECT audio the capture gate accepted — never admit anything it refused. It was
 *  a pure false-negative generator: a participant speaking quietly was transcribed while counting as
 *  silence toward `left_alone`, so the bot could leave a meeting it could hear. #850 measured 23.3%
 *  of frames in one real fixture sitting in exactly that peak-passes/RMS-fails band.
 *
 *  A cost decision ("don't pay Whisper for near-silence") is not a presence decision. Only a frame
 *  carrying no energy at all is silence here; anything the capture gate delivered is someone. */
export const REMOTE_AUDIO_ENERGY_FLOOR = 0;
/** How long a page-side stream-presence report stays trustworthy. Beyond it the presence signal is
 *  treated as UNKNOWN and aloneness behaves exactly as it did before the deaf guard existed — a
 *  page whose rescan loop died must not be able to hold a bot open on a stale "streams connected". */
export const DEFAULT_STREAM_PRESENCE_STALENESS_MS = 30_000;
/** How often a persisting capture-fault re-states itself in the log (it is a live incident, not a
 *  one-shot event: a bot that has been deaf for 40 minutes should say so more than once). */
export const CAPTURE_FAULT_LOG_INTERVAL_MS = 60_000;

export interface RemoteAudioActivitySnapshot {
  available: boolean;
  lastRemoteAudioAt?: number;
  /** Last DELIVERED remote frame, whatever its energy — the capture chain's liveness, as opposed to
   *  `lastRemoteAudioAt` which is presence. A zero-energy frame moves this and not that. */
  lastRemoteFrameAt?: number;
  /** Delivered remote frames since capture became ready (0 with a live stream => deaf, not silent). */
  framesDelivered?: number;
  /** Remote streams the page reported as CURRENTLY connected and carrying data. `undefined` means
   *  the lane never reports presence (gmeet captures per channel and has no mix): unknown, not zero. */
  streamsConnected?: number;
  /** When the last presence report of ANY count arrived (staleness check). */
  streamsObservedAt?: number;
  /** When the last presence report of a POSITIVE count arrived. Separate from the above because a
   *  remote track legitimately flaps `muted` between talk spurts (DTX): a bot must not conclude the
   *  room emptied because it sampled the gap. */
  streamsPresentAt?: number;
}

export interface RemoteAudioActivitySource {
  snapshot(): RemoteAudioActivitySnapshot;
}

export interface RemoteAudioActivityTap extends RemoteAudioActivitySource {
  /** Capture is attached and can distinguish silence from a missing signal. */
  ready(): void;
  /** Record one REMOTE frame's RMS energy. Local bot speech never enters this seam. */
  observeRemoteEnergy(energy: number): void;
  /** Capture stopped or failed; aloneness must fail closed until it is ready again. */
  unavailable(): void;
  /** Page-side report: how many remote streams are connected to the mix RIGHT NOW and carrying
   *  data (#1192). OPTIONAL by design, on both sides: the gmeet lane never calls it (presence stays
   *  unknown), and a tap that does not implement it — a test double, an embedder's own — keeps
   *  compiling and keeps today's behaviour. */
  observeStreamPresence?(count: number): void;
}

/** `capture-fault` = "these streams are connected and we are hearing NOTHING from them" — the bot is
 *  deaf, not alone. It is not a leave verdict and not a veto of one: it suppresses `left_alone` and
 *  says so loudly (#1192). */
export type AlonenessVerdict = 'alone' | 'not-alone' | 'unavailable' | 'capture-fault';

/** One deployment-selectable rule. Future presence checks can veto by returning not-alone. */
export interface AlonenessAdapter {
  readonly name: string;
  evaluate(snapshot: RemoteAudioActivitySnapshot, now: number, windowMs: number): AlonenessVerdict;
}

export interface TimerScheduler {
  setInterval(callback: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

export function createRemoteAudioActivityTap(options: {
  now?: () => number;
  energyFloor?: number;
} = {}): RemoteAudioActivityTap {
  const now = options.now ?? Date.now;
  const energyFloor = options.energyFloor ?? REMOTE_AUDIO_ENERGY_FLOOR;
  let state: RemoteAudioActivitySnapshot = { available: false };

  return {
    ready(): void {
      // Frame bookkeeping restarts with capture; the page's stream reports do NOT — they are a fact
      // about the ROOM, not about our capture chain, and survive a capture restart.
      state = { ...state, available: true, lastRemoteAudioAt: now(), lastRemoteFrameAt: undefined, framesDelivered: 0 };
    },
    observeRemoteEnergy(energy: number): void {
      if (!state.available || !Number.isFinite(energy)) return;
      // ARRIVAL is capture liveness — recorded for every delivered frame, including a digitally
      // silent one. That is the whole deaf/silent distinction (#1192): a capture chain that has
      // broken delivers nothing at all, while a quiet room still delivers frames.
      state = { ...state, lastRemoteFrameAt: now(), framesDelivered: (state.framesDelivered ?? 0) + 1 };
      // Digital silence (or a nonsense reading) is not presence; every other delivered frame is.
      if (energy <= 0 || energy < energyFloor) return;
      state = { ...state, lastRemoteAudioAt: now() };
    },
    unavailable(): void {
      // What the PAGE said about the room is not invalidated by our capture dying, so the presence
      // fields survive (they age out on their own staleness). Everything capture owns is dropped —
      // `available: false` is the fail-closed state, and it short-circuits every adapter.
      state = {
        available: false,
        streamsConnected: state.streamsConnected,
        streamsObservedAt: state.streamsObservedAt,
        streamsPresentAt: state.streamsPresentAt,
      };
    },
    observeStreamPresence(count: number): void {
      if (!Number.isFinite(count) || count < 0) return;
      const at = now();
      state = {
        ...state,
        streamsConnected: count,
        streamsObservedAt: at,
        streamsPresentAt: count > 0 ? at : state.streamsPresentAt,
      };
    },
    snapshot(): RemoteAudioActivitySnapshot {
      return { ...state };
    },
  };
}

export const silenceAlonenessAdapter: AlonenessAdapter = {
  name: 'silence',
  evaluate(snapshot, now, windowMs): AlonenessVerdict {
    if (!snapshot.available || snapshot.lastRemoteAudioAt === undefined) return 'unavailable';
    return now - snapshot.lastRemoteAudioAt >= windowMs ? 'alone' : 'not-alone';
  },
};

/**
 * The deaf-leave guard (#1192): connected streams delivering NOTHING are a broken capture chain,
 * never an empty room.
 *
 * ── the one predicate, and why it has to be one ──────────────────────────────────────────────────
 * Two open defects meet on this seam and pull the SAME latch in opposite directions:
 *
 *   #866 (PR #887, @Ayush7614) — an empty mixed-lane room never reaches `left_alone` at all,
 *     because remote-audio ready is latched only once `createMixedAudioCapture` starts, which needs
 *     at least one connected stream. A bot alone from the first second burns to the 4h cap.
 *   #1192 (this) — a bot whose capture chain dies mid-meeting hears nothing, lets the silence
 *     window expire, and leaves a LIVE meeting as `completed(left_alone)`: a recorded success.
 *
 * Frame arrival alone cannot tell those apart — both look like "no frames". #887 fixes #866 by
 * latching ready EARLIER (capture attached, could hear), which is the correct fix at the point of
 * introduction; but on its own it also removes the accidental protection that a never-started
 * capture could never latch ready, so it makes #1192 fire MORE readily, not less. Neither change is
 * safe alone. What separates the two cases is a bit neither PR had: whether any remote stream is
 * CURRENTLY connected and live. With it, one predicate covers both:
 *
 *   | capture attached | streams live now | frames arriving | truth                | outcome        |
 *   |------------------|------------------|-----------------|----------------------|----------------|
 *   | no               | —                | —               | not hearing yet      | fail closed    |
 *   | yes              | 0                | none            | empty room (#866)    | -> `left_alone` |
 *   | yes              | >=1              | yes             | someone there        | silence window |
 *   | yes              | >=1              | none for a window| deaf bot (#1192)    | HOLD + report  |
 *   | yes              | unknown (gmeet)  | —               | no presence oracle   | silence window |
 *
 * Row 2 is #887's; row 4 is this guard's; the guard is written so row 2 stays correct whether #887
 * has landed or not (before it, the row is simply unreachable — never wrong).
 *
 * It is a VETO-ONLY adapter and returns `alone` to mean "no objection" — abstaining, not voting to
 * leave. Composed with the silence adapter (the shipped default) that reads correctly; alone in an
 * `adapters` list it would abstain from everything, which is not a configuration worth having.
 *
 * It abstains whenever the presence signal cannot carry the decision:
 *   - the lane never reports presence (gmeet captures per channel — unknown => today's behaviour);
 *   - the last report is staler than `stalenessMs` (a dead page must not hold a bot open);
 *   - no stream has been reported present recently (the room really did empty — row 2);
 *   - frames are still arriving inside the window (capture is alive; silence is the room's, and a
 *     zero-energy frame keeps counting as silence exactly as before).
 */
export function createDeafCaptureGuardAdapter(options: { stalenessMs?: number } = {}): AlonenessAdapter {
  const stalenessMs = options.stalenessMs ?? DEFAULT_STREAM_PRESENCE_STALENESS_MS;
  return {
    name: 'deaf-capture-guard',
    evaluate(snapshot, now, windowMs): AlonenessVerdict {
      if (!snapshot.available) return 'alone';                                   // the monitor owns unavailable
      if (snapshot.streamsObservedAt === undefined) return 'alone';              // presence unknown
      if (now - snapshot.streamsObservedAt > stalenessMs) return 'alone';        // presence stale
      if (snapshot.streamsPresentAt === undefined) return 'alone';               // never any stream (row 2)
      if (now - snapshot.streamsPresentAt > stalenessMs) return 'alone';         // streams are gone (row 2)
      if (snapshot.lastRemoteFrameAt !== undefined && now - snapshot.lastRemoteFrameAt < windowMs) {
        return 'alone';                                                          // capture is delivering
      }
      return 'capture-fault';
    },
  };
}

export const deafCaptureGuardAdapter: AlonenessAdapter = createDeafCaptureGuardAdapter();

export function resolveAloneSilenceWindowMs(
  explicitEveryoneLeftTimeout: number | undefined,
  env: NodeJS.ProcessEnv = process.env,
  warn: (message: string) => void = (message) => console.warn(`[bot] ${message}`),
): number {
  if (typeof explicitEveryoneLeftTimeout === 'number'
    && Number.isFinite(explicitEveryoneLeftTimeout)
    && explicitEveryoneLeftTimeout > 0) {
    return explicitEveryoneLeftTimeout;
  }
  const raw = env.BOT_ALONE_SILENCE_WINDOW_MS;
  if (raw !== undefined && raw.trim() !== '') {
    const value = Number(raw);
    if (Number.isFinite(value) && value > 0) return value;
    warn(`BOT_ALONE_SILENCE_WINDOW_MS=${JSON.stringify(raw)} is invalid; using the 10-minute default`);
  }
  return DEFAULT_ALONE_SILENCE_WINDOW_MS;
}

export function createSilenceAlonenessSource(options: {
  activity: RemoteAudioActivitySource;
  windowMs: number;
  adapters?: readonly AlonenessAdapter[];
  now?: () => number;
  pollMs?: number;
  setInterval?: TimerScheduler['setInterval'];
  clearInterval?: TimerScheduler['clearInterval'];
  log?: (message: string) => void;
  /** Called AT MOST ONCE per subscription, the first time a capture-fault is suspected (#1192):
   *  the one cheap repair attempt (restart the page-side capture). Optional — with no hook the
   *  guard still holds the meeting open and keeps checking. */
  onCaptureFault?: () => void;
}): AlonenessSource {
  const now = options.now ?? Date.now;
  const pollMs = options.pollMs ?? DEFAULT_ALONENESS_POLL_MS;
  const adapters = options.adapters ?? [silenceAlonenessAdapter, deafCaptureGuardAdapter];
  const setIntervalFn = options.setInterval ?? ((callback, ms) => setInterval(callback, ms));
  const clearIntervalFn = options.clearInterval ?? ((handle) => clearInterval(handle as ReturnType<typeof setInterval>));
  const log = options.log ?? ((message) => console.log(`[bot] ${message}`));

  return {
    onAlone(callback): () => void {
      let handle: unknown;
      let stopped = false;
      let fired = false;
      /** Capture-fault incident state (#1192): when it started, when it last said so, and whether
       *  the single repair attempt has been spent. */
      let captureFaultSince: number | null = null;
      let captureFaultLoggedAt: number | null = null;
      let captureFaultRepairAttempted = false;

      const stop = (): void => {
        if (stopped) return;
        stopped = true;
        if (handle !== undefined) clearIntervalFn(handle);
      };
      const tick = (): void => {
        if (stopped || fired || adapters.length === 0) return;
        const at = now();
        const snapshot = options.activity.snapshot();
        let captureFault = false;
        for (const adapter of adapters) {
          const verdict = adapter.evaluate(snapshot, at, options.windowMs);
          // A deaf capture is not a leave and not a veto: it withholds the verdict and reports.
          if (verdict === 'capture-fault') { captureFault = true; continue; }
          if (verdict !== 'alone') return;
        }
        if (captureFault) {
          // Streams are connected and delivering nothing — the bot is deaf, not alone (#1192).
          // Hold the meeting open and keep checking; the orchestrator's maxActiveMs ceiling still
          // bounds the run, so holding can never mean running forever.
          if (captureFaultSince === null) captureFaultSince = at;
          if (captureFaultLoggedAt === null || at - captureFaultLoggedAt >= CAPTURE_FAULT_LOG_INTERVAL_MS) {
            captureFaultLoggedAt = at;
            log(`aloneness: capture-fault suspected (streams=${snapshot.streamsConnected}, no frames for window)`
              + ` — holding left_alone (frames_delivered=${snapshot.framesDelivered ?? 0},`
              + ` last_frame_at=${snapshot.lastRemoteFrameAt ?? 'never'}, window_ms=${options.windowMs},`
              + ` deaf_for_ms=${at - captureFaultSince})`);
          }
          if (!captureFaultRepairAttempted && options.onCaptureFault) {
            captureFaultRepairAttempted = true;
            log('aloneness: capture-fault — attempting one capture restart');
            try { options.onCaptureFault(); } catch { /* a repair attempt must never break the monitor */ }
          }
          return;
        }
        captureFaultSince = null;
        captureFaultLoggedAt = null;
        fired = true;
        stop();
        log(`aloneness: silence verdict (last_remote_audio_at=${snapshot.lastRemoteAudioAt}, window_ms=${options.windowMs})`);
        callback();
      };

      handle = setIntervalFn(tick, pollMs);
      tick();
      return stop;
    },
  };
}

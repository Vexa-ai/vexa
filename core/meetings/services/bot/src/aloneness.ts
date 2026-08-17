/** Active-phase aloneness derived from the remote-audio signal. */
import type { AlonenessSource } from './ports.js';

export const DEFAULT_ALONE_SILENCE_WINDOW_MS = 10 * 60 * 1000;
export const DEFAULT_ALONENESS_POLL_MS = 1_500;
/** Bounded fail-safe grace: once capture WAS ready, a prolonged unavailable (detached/stopped) tap
 *  while the bot is still active converges to `left_alone` after this window instead of idling
 *  forever (a live, attached capture is never unavailable; an ended/kicked call detaches it). */
export const DEFAULT_ALONE_UNAVAILABLE_GRACE_MS = 60_000;
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

export interface RemoteAudioActivitySnapshot {
  available: boolean;
  lastRemoteAudioAt?: number;
}

export interface RemoteAudioActivitySource {
  snapshot(): RemoteAudioActivitySnapshot;
}

export interface RemoteAudioActivityTap extends RemoteAudioActivitySource {
  /** Capture is attached and can distinguish silence from a missing signal. */
  ready(): void;
  /** Record one REMOTE frame's RMS energy. Local bot speech never enters this seam. */
  observeRemoteEnergy(energy: number): void;
  /** Capture stopped or failed; aloneness fails closed until it is ready again — and, if it has
   *  EVER been ready, converges to `left_alone` after the bounded unavailable grace (fail-safe). */
  unavailable(): void;
}

export type AlonenessVerdict = 'alone' | 'not-alone' | 'unavailable';

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
      state = { available: true, lastRemoteAudioAt: now() };
    },
    observeRemoteEnergy(energy: number): void {
      // Digital silence (or a nonsense reading) is not presence; every other delivered frame is.
      if (!state.available || !Number.isFinite(energy) || energy <= 0 || energy < energyFloor) return;
      state = { available: true, lastRemoteAudioAt: now() };
    },
    unavailable(): void {
      state = { available: false };
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

export function resolveAloneUnavailableGraceMs(
  env: NodeJS.ProcessEnv = process.env,
  warn: (message: string) => void = (message) => console.warn(`[bot] ${message}`),
): number {
  const raw = env.BOT_ALONE_UNAVAILABLE_GRACE_MS;
  if (raw !== undefined && raw.trim() !== '') {
    const value = Number(raw);
    if (Number.isFinite(value) && value > 0) return value;
    warn(`BOT_ALONE_UNAVAILABLE_GRACE_MS=${JSON.stringify(raw)} is invalid; using the 60s default`);
  }
  return DEFAULT_ALONE_UNAVAILABLE_GRACE_MS;
}

export function createSilenceAlonenessSource(options: {
  activity: RemoteAudioActivitySource;
  windowMs: number;
  /** Bounded fail-safe grace once capture HAS been ready (default 60s). */
  unavailableGraceMs?: number;
  adapters?: readonly AlonenessAdapter[];
  now?: () => number;
  pollMs?: number;
  setInterval?: TimerScheduler['setInterval'];
  clearInterval?: TimerScheduler['clearInterval'];
  log?: (message: string) => void;
}): AlonenessSource {
  const now = options.now ?? Date.now;
  const pollMs = options.pollMs ?? DEFAULT_ALONENESS_POLL_MS;
  const unavailableGraceMs = options.unavailableGraceMs ?? DEFAULT_ALONE_UNAVAILABLE_GRACE_MS;
  const adapters = options.adapters ?? [silenceAlonenessAdapter];
  const setIntervalFn = options.setInterval ?? ((callback, ms) => setInterval(callback, ms));
  const clearIntervalFn = options.clearInterval ?? ((handle) => clearInterval(handle as ReturnType<typeof setInterval>));
  const log = options.log ?? ((message) => console.log(`[bot] ${message}`));

  return {
    onAlone(callback): () => void {
      let handle: unknown;
      let stopped = false;
      let fired = false;
      /** Capture ever reached the ready state on this subscription (boot-race guard: a tap that
       *  has NEVER been ready is "no signal", not "detached" — it keeps failing closed). */
      let wasEverReady = false;
      let unavailableSince: number | null = null;

      const stop = (): void => {
        if (stopped) return;
        stopped = true;
        if (handle !== undefined) clearIntervalFn(handle);
      };
      const tick = (): void => {
        if (stopped || fired || adapters.length === 0) return;
        const at = now();
        const snapshot = options.activity.snapshot();
        if (!snapshot.available) {
          if (!wasEverReady) return; // never-ready tap: fail closed, keep polling
          // Capture WAS attached and is now unavailable (ended/kicked call detaches it, or the
          // page navigated away) — converge after the bounded grace instead of idling forever.
          if (unavailableSince === null) unavailableSince = at;
          if (at - unavailableSince >= unavailableGraceMs) {
            fired = true;
            stop();
            log(`aloneness: capture unavailable for ${unavailableGraceMs}ms while active — left_alone (fail-safe)`);
            callback();
          }
          return;
        }
        wasEverReady = true;
        unavailableSince = null;
        for (const adapter of adapters) {
          if (adapter.evaluate(snapshot, at, options.windowMs) !== 'alone') return;
        }
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

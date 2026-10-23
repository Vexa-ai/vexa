import { log } from './log.js';

/** What a caller above the client may read about the STT's back-pressure (structural: the lane does
 *  not import this package's classes). */
export interface SttPressure {
  /** True while requests are held: an overload pause, a half-open breaker, or windows queued. */
  backingOff(): boolean;
  /** Wall-clock ms at which the most recent hold ended (0 if none yet). A request's wait is measured
   *  from no earlier than this, so time spent held is never read as a slow STT. */
  lastBackoffEndMs(): number;
}

export interface SttGateConfig {
  /** Requests this bot may have at the STT at once. Default 8. */
  maxConcurrent?: number;
  /** Consecutive overload answers that open the breaker. Default 3. */
  breakerThreshold?: number;
  /** First overload backoff (ms) when the STT gives no Retry-After; doubles per consecutive overload. Default 2000. */
  backoffBaseMs?: number;
  /** Backoff ceiling (ms). Default 30000. */
  backoffMaxMs?: number;
  /** Longest Retry-After honoured (ms); a longer one is clipped to this. Default 120000. */
  retryAfterMaxMs?: number;
  /** Jitter source in [0,1) (tests). */
  random?: () => number;
}

/**
 * One per bot: every STT request passes through it. It bounds the requests in flight, and on an
 * overload answer (429, 503, a 5xx relaying an upstream 429) it pauses ALL submissions — for the
 * Retry-After the STT gave, else a jittered exponential backoff — instead of each request retrying on
 * its own schedule. After `breakerThreshold` consecutive overloads the breaker opens: once the pause
 * ends a single probe goes out, and the rest follow only when it succeeds. Held requests leave in the
 * order they arrived, so no window is lost or reordered while it waits.
 */
export class SttGate implements SttPressure {
  private readonly maxConcurrent: number;
  private readonly threshold: number;
  private readonly baseMs: number;
  private readonly maxMs: number;
  private readonly retryAfterMaxMs: number;
  private readonly random: () => number;
  private inFlight = 0;
  private queue: (() => void)[] = [];
  private pausedUntil = 0;
  private consecutive = 0;
  private open = false;
  private probing = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private lastEnd = 0;
  private peak = 0;
  private overloads = 0;

  constructor(config: SttGateConfig = {}) {
    this.maxConcurrent = Math.max(1, config.maxConcurrent ?? 8);
    this.threshold = Math.max(1, config.breakerThreshold ?? 3);
    this.baseMs = Math.max(1, config.backoffBaseMs ?? 2000);
    this.maxMs = Math.max(this.baseMs, config.backoffMaxMs ?? 30_000);
    this.retryAfterMaxMs = config.retryAfterMaxMs ?? 120_000;
    this.random = config.random ?? Math.random;
  }

  backingOff(): boolean {
    return Date.now() < this.pausedUntil || this.open || this.queue.length > 0;
  }

  lastBackoffEndMs(): number {
    const now = Date.now();
    return Math.max(this.lastEnd, this.pausedUntil <= now ? this.pausedUntil : 0);
  }

  stats(): { inFlight: number; queued: number; maxInFlight: number; overloads: number; breakerOpen: boolean } {
    return { inFlight: this.inFlight, queued: this.queue.length, maxInFlight: this.peak, overloads: this.overloads, breakerOpen: this.open };
  }

  /** Wait for a slot; resolves with the release function the caller must call once its request ends. */
  acquire(): Promise<() => void> {
    return new Promise((resolve) => {
      let released = false;
      const grant = () => {
        this.inFlight++;
        this.peak = Math.max(this.peak, this.inFlight);
        if (this.open) this.probing = true;
        resolve(() => {
          if (released) return;
          released = true;
          this.inFlight--;
          this.pump();
        });
      };
      if (this.queue.length === 0 && this.canGrant()) { grant(); return; }
      this.queue.push(grant);
      this.pump();
    });
  }

  /** An answer came back: the STT is serving again. Closes the breaker. */
  onSuccess(): void {
    if (this.open) {
      log('[SttGate] [BREAKER-CLOSED] STT answered — releasing held requests');
      this.lastEnd = Date.now();
    }
    this.consecutive = 0;
    this.open = false;
    this.probing = false;
    this.pump();
  }

  /** A request ended without an overload verdict (timeout, network, 4xx): a failed probe frees its turn. */
  onOtherFailure(): void {
    this.probing = false;
    this.pump();
  }

  /** The STT said it is overloaded. Pauses every submission and returns the pause (ms). */
  onOverload(retryAfterMs?: number): number {
    this.overloads++;
    this.consecutive++;
    this.probing = false;
    let delay: number;
    if (retryAfterMs !== undefined && retryAfterMs >= 0) {
      delay = Math.min(this.retryAfterMaxMs, retryAfterMs);
    } else {
      const exp = Math.min(this.maxMs, this.baseMs * 2 ** (this.consecutive - 1));
      delay = Math.round(exp / 2 + this.random() * (exp / 2));       // equal jitter: [exp/2, exp)
    }
    this.pausedUntil = Math.max(this.pausedUntil, Date.now() + delay);
    if (!this.open && this.consecutive >= this.threshold) {
      this.open = true;
      log(`[SttGate] [BREAKER-OPEN] ${this.consecutive} consecutive overload answers — all STT submissions paused ` +
          `${delay}ms, then one probe before the rest (${this.queue.length} held, ${this.inFlight} in flight)`);
    }
    this.pump();
    return delay;
  }

  private canGrant(): boolean {
    if (Date.now() < this.pausedUntil) return false;
    if (this.open) return !this.probing && this.inFlight === 0;
    return this.inFlight < this.maxConcurrent;
  }

  private pump(): void {
    while (this.queue.length && this.canGrant()) {
      const next = this.queue.shift()!;
      this.lastEnd = Date.now();
      next();
    }
    if (this.queue.length && !this.timer) {
      const wait = this.pausedUntil - Date.now();
      if (wait > 0) {
        // Not unref'd: a held request is pending work, and its pause must elapse to release it.
        this.timer = setTimeout(() => { this.timer = undefined; this.pump(); }, wait);
      }
    }
  }
}

/** Retry-After as ms: delta-seconds or an HTTP date. Undefined when absent or unreadable. */
export function parseRetryAfter(value: string | null | undefined, now = Date.now()): number | undefined {
  if (value == null) return undefined;
  const v = value.trim();
  if (v === '') return undefined;
  if (/^\d+(\.\d+)?$/.test(v)) return Math.round(Number(v) * 1000);
  const at = Date.parse(v);
  return Number.isFinite(at) ? Math.max(0, at - now) : undefined;
}

/** A 5xx body that relays an upstream rate limit or saturation is overload, not an outage. */
export function bodySaysOverloaded(detail: string | undefined): boolean {
  return /\b429\b|saturat|rate.?limit|too many requests|overload|capacity/i.test(detail ?? '');
}

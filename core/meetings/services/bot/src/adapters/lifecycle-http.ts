/**
 * lifecycle.v1 egress ADAPTER — HTTP callback to meeting-api.
 *
 * Implements the `LifecycleSink` port by POSTing each lifecycle.v1 event verbatim to the
 * meeting-api callback URL (`inv.meetingApiCallbackUrl`). 0.11 convention:
 *   • headers: `content-type: application/json` + (if set) `x-internal-secret: <internalSecret>`
 *   • body: the lifecycle.v1 event JSON, as-is (no envelope)
 *
 * L3-testable via an INJECTED `fetchImpl` (defaults to Node 22's native `fetch` — NO new dep).
 * The composition root builds the live adapter; the test injects a recording/failing fake.
 *
 * Robustness (P14): a lifecycle POST failure must NEVER crash the bot. `emit` retries with a
 * bounded backoff on a network error or a non-2xx response, then logs + gives up — it never
 * throws fatally out of `emit`. (A dropped status report is regrettable but must not strand a
 * seated bot or mask the terminal exit.)
 */
import type { LifecycleEvent } from '../contracts.js';
import type { LifecycleSink, PrimaryReachability } from '../ports.js';

/** The minimal fetch shape we depend on (a subset of the WHATWG `fetch`), so the test can
 *  inject a fake without pulling in DOM/undici types. */
export type FetchLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; body: string },
) => Promise<{ ok: boolean; status: number }>;

export interface HttpLifecycleSinkOptions {
  /** meeting-api's lifecycle.v1 callback URL (invocation.v1 `meetingApiCallbackUrl`). */
  callbackUrl: string;
  /** SECRET — sent as `x-internal-secret` when present (0.11 internal auth). */
  internalSecret?: string;
  /** Injected for the L3 test; defaults to Node 22's native global `fetch`. */
  fetchImpl?: FetchLike;
  /** Max POST attempts (1 try + retries). Default 3. */
  retries?: number;
  /** Base backoff (ms) between attempts; doubles each retry (bounded). Default 200ms. */
  backoffMs?: number;
  /** Sleep impl (injected so the test runs instantly). Default real setTimeout. */
  sleep?: (ms: number) => Promise<void>;
  /** Max attempts for the REACHABILITY probe (`emitReachable`, #530) — a longer bounded budget
   *  than a normal emit so it rides out transient CNI-programming lag, but capped WELL under the
   *  meeting-api `requested` grace. Default 6 (with `reachBackoffMs` 300 ⇒ ≤ ~9.3s total). */
  reachRetries?: number;
  /** Base backoff (ms) for the reachability probe; doubles each retry. Default 300ms. */
  reachBackoffMs?: number;
}

const realSleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/** Build the live HTTP lifecycle sink. `emit` POSTs the event with bounded retry/backoff and
 *  never throws — a permanent failure is logged and swallowed (the bot keeps running). */
export function createHttpLifecycleSink(opts: HttpLifecycleSinkOptions): LifecycleSink {
  const {
    callbackUrl,
    internalSecret,
    fetchImpl = globalThis.fetch as unknown as FetchLike,
    retries = 3,
    backoffMs = 200,
    sleep = realSleep,
    reachRetries = 6,
    reachBackoffMs = 300,
  } = opts;

  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (internalSecret) headers['x-internal-secret'] = internalSecret;

  const attempts = Math.max(1, retries);
  const reachAttempts = Math.max(1, reachRetries);

  async function emit(event: LifecycleEvent): Promise<void> {
    const body = JSON.stringify(event);
    let lastErr: string | undefined;
    for (let attempt = 1; attempt <= attempts; attempt++) {
      try {
        const res = await fetchImpl(callbackUrl, { method: 'POST', headers, body });
        if (res.ok) return; // 2xx — delivered
        lastErr = `HTTP ${res.status}`;
      } catch (e) {
        lastErr = (e as Error)?.message ?? String(e);
      }
      // Bounded exponential backoff before the next attempt (none after the last).
      if (attempt < attempts) await sleep(backoffMs * 2 ** (attempt - 1));
    }
    // Give up — log, never throw (a lifecycle POST failure must not crash the bot, P14).
    console.error(
      `[bot] lifecycle.v1 ${event.status} POST failed after ${attempts} attempt(s): ${lastErr ?? 'unknown'} (giving up)`,
    );
  }

  // The reachability gate's first-emit probe (#530). Emits the (joining) event AND reports whether
  // the primary channel is REACHABLE. Any HTTP response — 2xx OR non-2xx — proves the channel is
  // up (P18) and returns `reachable` immediately (near-zero latency on the fast path). Only when
  // EVERY attempt fails at the network layer (no response — the CNI-lag signature) is it
  // `unreachable`. Never throws (P14).
  async function emitReachable(event: LifecycleEvent): Promise<PrimaryReachability> {
    const body = JSON.stringify(event);
    let lastErr: string | undefined;
    for (let attempt = 1; attempt <= reachAttempts; attempt++) {
      try {
        // A response of ANY status means the callback host answered → the channel is up.
        await fetchImpl(callbackUrl, { method: 'POST', headers, body });
        return 'reachable';
      } catch (e) {
        lastErr = (e as Error)?.message ?? String(e);
      }
      if (attempt < reachAttempts) await sleep(reachBackoffMs * 2 ** (attempt - 1));
    }
    console.error(
      `[bot] lifecycle.v1 ${event.status} reachability probe: primary channel UNREACHABLE after ${reachAttempts} attempt(s): ${lastErr ?? 'unknown'}`,
    );
    return 'unreachable';
  }

  return { emit, emitReachable };
}

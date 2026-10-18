/** transientRetry — how long the chat waits before sending the same message again after a fault
 *  that clears by itself (`faults.isTransientFault`). One schedule for the streamed turn
 *  (`chatStream`) and for a message submitted while a turn runs (`inbox`): 1 s, 2 s, 4 s, 8 s, 8 s,
 *  never sooner than the server's `Retry-After`, for at most ~30 s of waiting in all. */

export const TRANSIENT_BACKOFF_BASE_MS = 1000;
export const TRANSIENT_BACKOFF_CAP_MS = 8000;
export const DEFAULT_TRANSIENT_BUDGET_MS = 30000;

/** `Retry-After` in milliseconds — seconds as a number or a string; an HTTP date or junk is 0. */
export function retryAfterMs(v: unknown): number {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
  return Number.isFinite(n) && n > 0 ? Math.min(n, 60) * 1000 : 0;
}

/** The wait before retry number `attempt` (0-based), honouring the server's Retry-After. */
export function transientWait(attempt: number, retryAfter: number): number {
  return Math.max(Math.min(TRANSIENT_BACKOFF_CAP_MS, TRANSIENT_BACKOFF_BASE_MS * 2 ** attempt), retryAfter);
}

/** Statuses an edge answers with when it means "not now". */
export const TRANSIENT_STATUS: ReadonlySet<number> = new Set([429, 502, 503, 504]);

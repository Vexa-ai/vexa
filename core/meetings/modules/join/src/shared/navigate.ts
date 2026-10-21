import type { Page } from "playwright";
import { log as hostLog } from "../_host";

// The first navigation of every platform's join. One transient socket error on the bot's
// egress path (a reset connection, a socket torn down mid-handshake) is not a verdict on the
// meeting, so the load is retried a bounded number of times — but only for transport-level
// net::ERR codes. A closed page/context/browser, an invalid URL, or any other error is
// rethrown at once: retrying it would only delay the same failure.

/** Chromium transport errors that a fresh attempt can plausibly clear. */
export const TRANSIENT_NAVIGATION_ERROR =
  /net::ERR_(CONNECTION_(CLOSED|RESET|ABORTED|REFUSED|FAILED)|SOCKET_NOT_CONNECTED|EMPTY_RESPONSE|NETWORK_CHANGED|TIMED_OUT|HTTP2_PROTOCOL_ERROR|QUIC_PROTOCOL_ERROR|SSL_PROTOCOL_ERROR|NAME_NOT_RESOLVED|ADDRESS_UNREACHABLE)/;

const CLOSED_TARGET = /Target page, context or browser has been closed|Target closed|Page closed/i;

export type GotoWaitUntil = "load" | "domcontentloaded" | "networkidle" | "commit";

export interface GotoWithRetryOptions {
  waitUntil: GotoWaitUntil;
  /** Per-attempt navigation timeout. */
  timeoutMs?: number;
  /** Total attempts, the first included. */
  attempts?: number;
  /** Wait before retry n (1-based); the last entry repeats if attempts outrun the list. */
  backoffMs?: number[];
  log?: (message: string) => void;
  sleep?: (ms: number) => Promise<void>;
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export async function gotoWithRetry(
  page: Pick<Page, "goto">,
  url: string,
  {
    waitUntil,
    timeoutMs = 60000,
    attempts = 3,
    backoffMs = [1000, 3000],
    log = hostLog,
    sleep = defaultSleep,
  }: GotoWithRetryOptions,
): Promise<void> {
  for (let attempt = 1; ; attempt++) {
    try {
      await page.goto(url, { waitUntil, timeout: timeoutMs });
      if (attempt > 1) log(`[Navigate] Loaded on attempt ${attempt}/${attempts}`);
      return;
    } catch (err) {
      const message = messageOf(err);
      const code = CLOSED_TARGET.test(message) ? null : message.match(TRANSIENT_NAVIGATION_ERROR)?.[0];
      if (!code) throw err;
      if (attempt >= attempts) {
        log(`[Navigate] Attempt ${attempt}/${attempts} failed with ${code} — giving up`);
        throw new Error(`page.goto failed after ${attempts} attempts: ${message}`);
      }
      const wait = backoffMs[Math.min(attempt - 1, backoffMs.length - 1)] ?? 0;
      log(`[Navigate] Attempt ${attempt}/${attempts} failed with ${code} — retrying in ${wait}ms`);
      await sleep(wait);
    }
  }
}

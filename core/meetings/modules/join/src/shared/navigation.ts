/**
 * The first navigation to the meeting page, retried when the NETWORK failed it (a flaky external:
 * DNS or the connection), never when the page answered.
 *
 * Meeting 282 (claude-rc, 3 Nov 2026): Chromium could not resolve meet.google.com while the host
 * was overloaded; `page.goto` threw `net::ERR_NAME_NOT_RESOLVED` and the bot failed 0.4 s after
 * starting, as a bare `join_failure`. The same room took the next bot three minutes later. So a
 * network error on this one navigation is retried a bounded number of times, and when every try
 * fails the terminal names the network code (`navigation_network_error: net::ERR_…`) instead of a
 * generic failure. Anything else Playwright throws (a timeout, a crash, an HTTP error page) is
 * rethrown at once, URL-redacted, as before.
 */
import type { Page } from "playwright";
import { log } from "../_host";
import { AdmissionError } from "./admission";
import { rethrowRedacted } from "./url-redaction";

export const NAVIGATION_NETWORK_ERROR = "navigation_network_error";

/** Chromium net errors that mean the request never reached the meeting platform. */
const NETWORK_ERROR =
  /net::(ERR_NAME_NOT_RESOLVED|ERR_NAME_RESOLUTION_FAILED|ERR_INTERNET_DISCONNECTED|ERR_NETWORK_CHANGED|ERR_ADDRESS_UNREACHABLE|ERR_CONNECTION_(?:RESET|REFUSED|CLOSED|TIMED_OUT)|ERR_TIMED_OUT)\b/;

/** Waits before each retry: 3 tries after the first, about 17 s in all. */
export const NAVIGATION_RETRY_DELAYS_MS: readonly number[] = [2000, 5000, 10000];

/** The `net::ERR_…` code of a navigation error the network caused, or null. */
export function networkErrorCode(error: unknown): string | null {
  const text = error instanceof Error ? error.message : String(error ?? "");
  const m = NETWORK_ERROR.exec(text);
  return m ? `net::${m[1]}` : null;
}

/** Every navigation failed on the network. A `join_failure`: the bot never reached the meeting. */
export class NavigationNetworkError extends AdmissionError {
  readonly code: string;
  readonly attempts: number;
  constructor(code: string, attempts: number) {
    super(
      "join_failure",
      `${NAVIGATION_NETWORK_ERROR}: ${code} on ${attempts} of ${attempts} navigation${attempts === 1 ? "" : "s"} ` +
        `to the meeting page (DNS or connection); the bot never reached the meeting`,
    );
    this.name = "NavigationNetworkError";
    this.code = code;
    this.attempts = attempts;
  }
}

type GotoOptions = Parameters<Page["goto"]>[1];

/**
 * `page.goto(url, options)` with the network retry. `sleep` is injectable for tests.
 */
export async function gotoMeetingPage(
  page: Pick<Page, "goto">,
  url: string,
  options: GotoOptions,
  delaysMs: readonly number[] = NAVIGATION_RETRY_DELAYS_MS,
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
): Promise<void> {
  for (let attempt = 1; ; attempt++) {
    try {
      await page.goto(url, options);
      if (attempt > 1) log(`[nav-retry] reached the meeting page on navigation ${attempt}`);
      return;
    } catch (error) {
      const code = networkErrorCode(error);
      if (!code) rethrowRedacted(error);
      if (attempt > delaysMs.length) {
        log(`[nav-retry] ${code} on navigation ${attempt} of ${attempt}; giving up`);
        throw new NavigationNetworkError(code!, attempt);
      }
      const wait = delaysMs[attempt - 1];
      log(`[nav-retry] ${code} on navigation ${attempt}; retrying in ${wait} ms (flaky network)`);
      await sleep(wait);
    }
  }
}

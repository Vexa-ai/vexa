/**
 * Google Meet refusing the knock itself — "You can't join this video call".
 *
 * Reproduced on the join harness (bbb, 29 Oct 2026), seen in prod meeting 34824 and in a self-hosted
 * log (meeting 121, 29 Oct): Meet answers one of two calls with HTTP 403 (gRPC PERMISSION_DENIED,
 * "The caller does not have permission"):
 *   - `MeetingSpaceService/ResolveMeetingSpace`, ~3.5 s after the lobby loads, before any click;
 *   - `MeetingDeviceService/CreateMeetingDevice`, right after "Ask to join" / "Join now".
 * Either way no meeting device exists, so the host never receives a request; the page throws
 * `DisconnectedError, EndCause = 72, StartupCode = 200` and shows "You can't join this video call"
 * with a "Return to home screen" button, then redirects to workspace.google.com after 60 s.
 * (ResolveMeetingSpace answering 404 is a meeting code that does not exist: not this.)
 *
 * Before this module the bot read that screen's button as the waiting room (#471), reported
 * `awaiting_admission`, and polled a page with nothing behind it for its whole lobby budget.
 *
 * The 403 is FLAKY on Google's side, not deterministic: on the harness (bbb, our own Meet rooms,
 * 29 Oct 16:18-16:52Z) 9 of 22 knocks that reached the click were refused, and every refused bot got
 * through on its second or third knock (same room, same browser). In prod (31 Oct-1 Nov) refusals came
 * in runs: three knocks seconds apart were all refused, while a fresh bot a minute later got in. So the
 * bot re-knocks a bounded number of times with growing waits and a fresh guest state, and when Meet
 * keeps refusing it fails with a typed `denial` whose message names the refusal — never a lobby wait,
 * never "nobody admitted the bot".
 */
import { Page } from "playwright";
import { log } from "../_host";
import { AdmissionError } from "../shared/admission";
import { googleJoinRefusedIndicators } from "./selectors";

/** Reason code at the head of every refusal message; join evidence reads it as a platform refusal. */
export const GOOGLE_JOIN_REFUSED = "google_meet_join_refused";

/** Knocks per join, the first included. Meet's refusal is flaky; a meeting that refuses every one of
 *  these is reported as refused rather than knocked on until the lobby budget runs out. */
export const GOOGLE_JOIN_MAX_KNOCKS = 5;

/**
 * Wait before knock 2, 3, 4, 5. Prod, 31 Oct-1 Nov 2026: 23 of 90 Meet runs were refused on all 3 knocks
 * taken ~5 s apart on the same page, while a fresh bot a minute or more later usually got in (3 of 4
 * meetings on 1 Nov). Knocking again within seconds meets the same refusal; time and a fresh Google
 * state are what changed. 15 + 30 + 60 + 120 s = 225 s of waiting, and the lobby budget still bounds
 * every wait.
 */
export const GOOGLE_JOIN_REKNOCK_BACKOFF_MS: readonly number[] = [15_000, 30_000, 60_000, 120_000];

/** The origins whose cookies and storage make up an anonymous guest's Google identity. */
const GOOGLE_ORIGINS = ["https://meet.google.com", "https://accounts.google.com", "https://www.google.com"];

/**
 * Drop the anonymous guest's Google state before a re-knock, so the next knock reaches Meet as a new
 * guest would: the context's cookies, plus every storage kind of the Google origins (Chromium, via CDP).
 * Never called for a signed-in bot, whose persistent profile is its identity. Best effort: a wipe that
 * fails leaves the knock as it was before this change, so it is logged, never thrown.
 */
export async function freshenGoogleGuestState(page: Page): Promise<void> {
  try {
    await page.context().clearCookies();
  } catch (e) {
    log(`⚠️ could not clear cookies before the re-knock: ${(e as Error)?.message ?? e}`);
  }
  try {
    const cdp = await page.context().newCDPSession(page);
    for (const origin of GOOGLE_ORIGINS) {
      await cdp.send("Storage.clearDataForOrigin", { origin, storageTypes: "all" }).catch(() => {});
    }
    await cdp.detach().catch(() => {});
  } catch (e) {
    log(`⚠️ could not clear Google site storage before the re-knock: ${(e as Error)?.message ?? e}`);
  }
}

export interface KnockOptions {
  /** Wipe the guest's Google state between knocks (anonymous bots only). */
  freshen?: boolean;
  /** Injected for tests. */
  sleep?: (ms: number) => Promise<void>;
  freshenState?: (page: Page) => Promise<void>;
}

const sleepFor = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

// The two calls whose 403 is Meet refusing this join. Group 1 names the call in the evidence.
const REFUSING_RPC = /(MeetingSpaceService\/ResolveMeetingSpace|MeetingDeviceService\/CreateMeetingDevice)/;

// Per page: the refused call of the CURRENT knock ("<call> answered HTTP 403"), and whether the
// response listener is already attached. WeakMap/WeakSet so nothing outlives the page.
const refusedCall = new WeakMap<object, string>();
const watched = new WeakSet<object>();

/**
 * Listen for Meet refusing the join. Idempotent per page; call it before navigating to the meeting
 * (ResolveMeetingSpace answers during the lobby load). The network answer is the primary signal: it does not depend on the UI language and
 * it outlives the refusal screen (which Meet replaces with a redirect after 60 s).
 */
export function watchGoogleJoinRefusal(page: Page): void {
  const key = page as unknown as object;
  if (watched.has(key) || typeof (page as any).on !== "function") return;
  watched.add(key);
  page.on("response", (response) => {
    try {
      const call = response.status() === 403 ? REFUSING_RPC.exec(response.url())?.[1] : undefined;
      if (call) {
        const what = `${call.split("/")[1]} answered HTTP 403`;
        refusedCall.set(key, what);
        log(`🚫 Google Meet refused the join: ${what}`);
      }
    } catch { /* a response we cannot read is not evidence */ }
  });
}

/** Forget the previous knock's refusal before knocking again on the same page. */
export function resetGoogleJoinRefusal(page: Page): void {
  refusedCall.delete(page as unknown as object);
}

/** What shows Meet refused this knock, or null. Either signal alone is enough. */
export async function checkForGoogleJoinRefused(page: Page): Promise<string | null> {
  const evidence: string[] = [];
  const call = refusedCall.get(page as unknown as object);
  if (call) evidence.push(call);
  for (const selector of googleJoinRefusedIndicators) {
    const visible = await page.locator(selector).first().isVisible().catch(() => false);
    if (visible) {
      evidence.push(`Meet showed "You can't join this video call"`);
      break;
    }
  }
  return evidence.length ? evidence.join("; ") : null;
}

/** One refused knock. A typed `denial`: Meet said no, and no host was ever asked. */
export class GoogleJoinRefusedError extends AdmissionError {
  readonly evidence: string;
  constructor(evidence: string, knocks = 1) {
    super(
      "denial",
      `${GOOGLE_JOIN_REFUSED}: Google Meet refused the join on ${knocks} of ${knocks} knock${knocks === 1 ? "" : "s"} (${evidence}); ` +
        `the host never received a request to admit the bot`,
    );
    this.name = "GoogleJoinRefusedError";
    this.evidence = evidence;
  }
}

export async function throwIfGoogleJoinRefused(page: Page, context: string): Promise<void> {
  const evidence = await checkForGoogleJoinRefused(page);
  if (evidence) {
    log(`🚫 Google Meet refused the join (${evidence}, ${context})`);
    throw new GoogleJoinRefusedError(evidence);
  }
}

/**
 * Run one knock (`knock` = join click + admission wait) and knock again while Meet refuses it, up to
 * GOOGLE_JOIN_MAX_KNOCKS, waiting GOOGLE_JOIN_REKNOCK_BACKOFF_MS before each re-knock and, for an
 * anonymous bot, starting it from a fresh Google state. Any other outcome — admitted, denied by the
 * host, a lobby timeout, a crash — ends the loop as it is. `knock` receives the lobby budget still
 * left, so neither the waits nor the re-knocks extend the wait the control plane issued.
 */
export async function knockUntilGoogleAccepts(
  page: Page,
  timeoutMs: number,
  knock: (remainingMs: number) => Promise<boolean>,
  now: () => number = Date.now,
  opts: KnockOptions = {},
): Promise<boolean> {
  const sleep = opts.sleep ?? sleepFor;
  const freshenState = opts.freshenState ?? freshenGoogleGuestState;
  const deadline = now() + timeoutMs;
  for (let attempt = 1; ; attempt++) {
    resetGoogleJoinRefusal(page);
    try {
      return await knock(Math.max(deadline - now(), 0));
    } catch (e) {
      if (!(e instanceof GoogleJoinRefusedError)) throw e;
      const wait = GOOGLE_JOIN_REKNOCK_BACKOFF_MS[Math.min(attempt - 1, GOOGLE_JOIN_REKNOCK_BACKOFF_MS.length - 1)];
      // A wait that would use up the budget leaves no time to knock: report the refusal now.
      if (attempt >= GOOGLE_JOIN_MAX_KNOCKS || deadline - now() <= wait) {
        throw new GoogleJoinRefusedError(e.evidence, attempt);
      }
      log(`🔁 Google Meet refused knock ${attempt} of ${GOOGLE_JOIN_MAX_KNOCKS} (${e.evidence}); knocking again in ${Math.round(wait / 1000)} s${opts.freshen ? " as a fresh guest" : ""}`);
      await sleep(wait);
      if (opts.freshen) await freshenState(page);
    }
  }
}

/**
 * Google Meet refusing the knock itself — "You can't join this video call".
 *
 * Reproduced on the join harness (bbb, 29 Oct 2026) and seen in prod meeting 34824: right after the
 * bot clicks "Ask to join" / "Join now", Meet's `MeetingDeviceService/CreateMeetingDevice` call
 * answers HTTP 403. No meeting device is created, so the host never receives a request; the page
 * throws `DisconnectedError, EndCause = 72, StartupCode = 200` and shows "You can't join this video
 * call" with a "Return to home screen" button, then redirects to workspace.google.com after 60 s.
 *
 * Before this module the bot read that screen's button as the waiting room (#471), reported
 * `awaiting_admission`, and polled a page with nothing behind it for its whole lobby budget.
 *
 * The 403 is FLAKY on Google's side, not deterministic: on the harness (bbb, our own Meet rooms,
 * 29 Oct 16:18-16:52Z) 9 of 22 knocks that reached the click were refused, and every refused bot got
 * through on its second or third knock (same room, same browser). So the bot re-knocks a bounded
 * number of times, and when Meet keeps refusing it fails fast with a typed `denial` whose message
 * names the refusal — never a lobby wait, never "nobody admitted the bot".
 */
import { Page } from "playwright";
import { log } from "../_host";
import { AdmissionError } from "../shared/admission";
import { googleJoinRefusedIndicators } from "./selectors";

/** Reason code at the head of every refusal message; join evidence reads it as a platform refusal. */
export const GOOGLE_JOIN_REFUSED = "google_meet_join_refused";

/** Knocks per join, the first included. Meet's refusal is flaky; a meeting that refuses every one of
 *  these is reported as refused rather than knocked on until the lobby budget runs out. */
export const GOOGLE_JOIN_MAX_KNOCKS = 3;

const CREATE_MEETING_DEVICE = /MeetingDeviceService\/CreateMeetingDevice/;

// Per page: the refused CreateMeetingDevice status of the CURRENT knock, and whether the
// response listener is already attached. WeakMap/WeakSet so nothing outlives the page.
const refusedStatus = new WeakMap<object, number>();
const watched = new WeakSet<object>();

/**
 * Listen for Meet refusing the knock's device creation. Idempotent per page; call it before the
 * join click. The network answer is the primary signal: it does not depend on the UI language and
 * it outlives the refusal screen (which Meet replaces with a redirect after 60 s).
 */
export function watchGoogleJoinRefusal(page: Page): void {
  const key = page as unknown as object;
  if (watched.has(key) || typeof (page as any).on !== "function") return;
  watched.add(key);
  page.on("response", (response) => {
    try {
      if (response.status() === 403 && CREATE_MEETING_DEVICE.test(response.url())) {
        refusedStatus.set(key, 403);
        log(`🚫 Google Meet refused the join: CreateMeetingDevice answered HTTP 403`);
      }
    } catch { /* a response we cannot read is not evidence */ }
  });
}

/** Forget the previous knock's refusal before knocking again on the same page. */
export function resetGoogleJoinRefusal(page: Page): void {
  refusedStatus.delete(page as unknown as object);
}

/** What shows Meet refused this knock, or null. Either signal alone is enough. */
export async function checkForGoogleJoinRefused(page: Page): Promise<string | null> {
  const evidence: string[] = [];
  const status = refusedStatus.get(page as unknown as object);
  if (status) evidence.push(`CreateMeetingDevice answered HTTP ${status}`);
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
 * GOOGLE_JOIN_MAX_KNOCKS. Any other outcome — admitted, denied by the host, a lobby timeout, a crash
 * — ends the loop as it is. `knock` receives the lobby budget still left, so re-knocks never extend
 * the wait the control plane issued.
 */
export async function knockUntilGoogleAccepts(
  page: Page,
  timeoutMs: number,
  knock: (remainingMs: number) => Promise<boolean>,
  now: () => number = Date.now,
): Promise<boolean> {
  const deadline = now() + timeoutMs;
  for (let attempt = 1; ; attempt++) {
    resetGoogleJoinRefusal(page);
    try {
      return await knock(Math.max(deadline - now(), 0));
    } catch (e) {
      if (!(e instanceof GoogleJoinRefusedError)) throw e;
      if (attempt >= GOOGLE_JOIN_MAX_KNOCKS || deadline - now() <= 0) {
        throw new GoogleJoinRefusedError(e.evidence, attempt);
      }
      log(`🔁 Google Meet refused knock ${attempt} of ${GOOGLE_JOIN_MAX_KNOCKS} (${e.evidence}); knocking again`);
    }
  }
}

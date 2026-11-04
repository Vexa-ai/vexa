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

/** Reason code at the head of every refusal message but the guest-block one (below); join evidence
 *  reads it as a platform refusal, and meeting-api starts a new bot pod on it (meet_respawn.py). */
export const GOOGLE_JOIN_REFUSED = "google_meet_join_refused";

/** Meet's 403 message when the meeting itself does not admit a guest from a browser (bbb, 4 Nov 2026:
 *  a room whose owner allows only its own app refused 10 of 10 knocks with it; the flaky refusal says
 *  only "The caller does not have permission"). Every knock gets the same answer, so the bot does not
 *  knock again, and the message leaves out GOOGLE_JOIN_REFUSED so no new pod is started either. */
export const GOOGLE_GUEST_BLOCKED = "cannot resolve this meeting due to the client";

/** Knocks per join, the first included, when nothing is configured. Meet's refusal is independent per
 *  knock (bbb join matrix, 2 Nov 2026: ~48% of knocks refused on bbb, ~64% in prod, no difference by
 *  node, egress IP, image or fresh browser), so the knock count is the lever: at 64%, 3 knocks fail 26%
 *  and 10 knocks fail ~1%. A meeting that refuses every knock is reported as refused. */
export const GOOGLE_JOIN_MAX_KNOCKS = 10;

/** Extra wait before each re-knock, when nothing is configured: none. The re-knock itself (navigate,
 *  fill, click) takes ~11 s, the spacing prod has always had and the one validated on 2 Nov. Longer
 *  waits plus the wipe did worse in that matrix (13 of 20 in vs prod 15 of 20). */
export const GOOGLE_JOIN_REKNOCK_BACKOFF_MS: readonly number[] = [];

/** Bounds a configured value must respect; anything outside falls back to the default, with a log line. */
export const GOOGLE_JOIN_KNOCKS_RANGE = { min: 1, max: 20 } as const;
export const GOOGLE_JOIN_REKNOCK_WAIT_MAX_MS = 120_000;
export const GOOGLE_JOIN_REKNOCK_WAIT_TOTAL_MAX_MS = 600_000;

export interface GoogleKnockConfig {
  maxKnocks: number;
  /** Extra wait before knocks 2..n; the last value repeats. Empty = no extra wait. */
  backoffMs: readonly number[];
  /** Wipe the anonymous guest's Google cookies and storage before a re-knock. */
  wipe: boolean;
}

/**
 * The knock settings of this deployment, read from the bot's environment, so they change without a new
 * image (the runtime forwards them into every meeting-bot pod from its own environment):
 *   VEXA_GOOGLE_JOIN_MAX_KNOCKS     integer 1..20                       default 10
 *   VEXA_GOOGLE_REKNOCK_BACKOFF_MS  comma-separated ms, each 0..120000,  default "" (no extra wait)
 *                                   sum of the waits up to knock N ≤ 600000
 *   VEXA_GOOGLE_REKNOCK_WIPE        "1"/"true" wipes, anything else not  default off
 * An invalid value never fails a join: that one setting falls back to its default and a line is logged.
 * The issued lobby budget still bounds every knock and wait.
 */
export function googleKnockConfig(env: Record<string, string | undefined> = process.env): GoogleKnockConfig {
  let maxKnocks = GOOGLE_JOIN_MAX_KNOCKS;
  const rawKnocks = (env.VEXA_GOOGLE_JOIN_MAX_KNOCKS || "").trim();
  if (rawKnocks) {
    const n = Number(rawKnocks);
    if (Number.isInteger(n) && n >= GOOGLE_JOIN_KNOCKS_RANGE.min && n <= GOOGLE_JOIN_KNOCKS_RANGE.max) {
      maxKnocks = n;
    } else {
      log(`⚠️ VEXA_GOOGLE_JOIN_MAX_KNOCKS="${rawKnocks}" is not an integer ${GOOGLE_JOIN_KNOCKS_RANGE.min}..${GOOGLE_JOIN_KNOCKS_RANGE.max}; using ${GOOGLE_JOIN_MAX_KNOCKS}`);
    }
  }

  let backoffMs: readonly number[] = GOOGLE_JOIN_REKNOCK_BACKOFF_MS;
  const rawBackoff = (env.VEXA_GOOGLE_REKNOCK_BACKOFF_MS || "").trim();
  if (rawBackoff) {
    const ms = rawBackoff.split(",").map((x) => Number(x.trim()));
    const each = ms.every((n) => Number.isInteger(n) && n >= 0 && n <= GOOGLE_JOIN_REKNOCK_WAIT_MAX_MS);
    let total = 0;
    for (let k = 0; k < maxKnocks - 1; k++) total += ms[Math.min(k, ms.length - 1)] ?? 0;
    if (ms.length && each && total <= GOOGLE_JOIN_REKNOCK_WAIT_TOTAL_MAX_MS) {
      backoffMs = ms;
    } else {
      log(`⚠️ VEXA_GOOGLE_REKNOCK_BACKOFF_MS="${rawBackoff}" is not a list of 0..${GOOGLE_JOIN_REKNOCK_WAIT_MAX_MS} ms waits totalling ≤ ${GOOGLE_JOIN_REKNOCK_WAIT_TOTAL_MAX_MS} ms; using no extra wait`);
    }
  }

  const rawWipe = (env.VEXA_GOOGLE_REKNOCK_WIPE || "").trim().toLowerCase();
  const wipe = rawWipe === "1" || rawWipe === "true";
  return { maxKnocks, backoffMs, wipe };
}

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
  /** Waits before knocks 2..n (default GOOGLE_JOIN_REKNOCK_BACKOFF_MS: none). */
  backoffMs?: readonly number[];
  /** Knocks per join, the first included (default GOOGLE_JOIN_MAX_KNOCKS). */
  maxKnocks?: number;
  /** Injected for tests. */
  sleep?: (ms: number) => Promise<void>;
  freshenState?: (page: Page) => Promise<void>;
}

const sleepFor = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

// The two calls whose 403 is Meet refusing this join. Group 1 names the call in the evidence.
const REFUSING_RPC = /(MeetingSpaceService\/ResolveMeetingSpace|MeetingDeviceService\/CreateMeetingDevice)/;

// Per page: the refused call of the CURRENT knock ("<call> answered HTTP 403"), the message in its
// response body (read as it arrives), and whether the response listener is already attached.
// WeakMap/WeakSet so nothing outlives the page.
const refusedCall = new WeakMap<object, string>();
const refusedMessage = new WeakMap<object, Promise<string | null>>();
const watched = new WeakSet<object>();

/** How long a refusal check waits for the refused call's body before reporting without it. */
const REFUSAL_BODY_WAIT_MS = 2_000;

/** The message of a 403 body. Meet answers with a protobuf google.rpc.Status: field 1 the code,
 *  field 2 the message, field 3 the error details. Returns field 2, at most 200 chars, or null. */
export function refusalMessage(body: Buffer): string | null {
  let i = 0;
  const varint = (): number => {
    let value = 0;
    for (let shift = 0; i < body.length && shift < 35; shift += 7) {
      const b = body[i++];
      value += (b & 0x7f) * 2 ** shift;
      if (!(b & 0x80)) return value;
    }
    return -1;
  };
  while (i < body.length) {
    const tag = varint();
    if (tag < 0) return null;
    const type = tag & 7;
    if (type === 0) { if (varint() < 0) return null; continue; }
    if (type !== 2) return null;
    const len = varint();
    if (len < 0 || i + len > body.length) return null;
    const bytes = body.subarray(i, i + len);
    i += len;
    if (tag >>> 3 === 2) return bytes.toString("utf8").trim().slice(0, 200) || null;
  }
  return null;
}
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
        refusedMessage.set(key, typeof (response as any).body === "function"
          ? response.body().then(refusalMessage).catch(() => null)
          : Promise.resolve(null));
        log(`🚫 Google Meet refused the join: ${what}`);
      }
    } catch { /* a response we cannot read is not evidence */ }
  });
}

/** Forget the previous knock's refusal before knocking again on the same page. */
export function resetGoogleJoinRefusal(page: Page): void {
  refusedCall.delete(page as unknown as object);
  refusedMessage.delete(page as unknown as object);
}

/** What shows Meet refused this knock, or null. Either signal alone is enough. */
export async function checkForGoogleJoinRefused(page: Page): Promise<string | null> {
  const evidence: string[] = [];
  const call = refusedCall.get(page as unknown as object);
  if (call) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const message = await Promise.race([
      refusedMessage.get(page as unknown as object) ?? Promise.resolve(null),
      new Promise<null>((resolve) => { timer = setTimeout(() => resolve(null), REFUSAL_BODY_WAIT_MS); }),
    ]);
    clearTimeout(timer);
    evidence.push(message ? `${call}: ${message}` : call);
  }
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
  /** The meeting does not admit a guest from a browser (GOOGLE_GUEST_BLOCKED): no knock can pass. */
  readonly guestBlocked: boolean;
  constructor(evidence: string, knocks = 1) {
    const guestBlocked = evidence.includes(GOOGLE_GUEST_BLOCKED);
    super(
      "denial",
      guestBlocked
        ? `Google Meet refused the join: this meeting does not admit guests joining from a browser (${evidence}); ` +
          `the host never received a request to admit the bot`
        : `${GOOGLE_JOIN_REFUSED}: Google Meet refused the join on ${knocks} of ${knocks} knock${knocks === 1 ? "" : "s"} (${evidence}); ` +
          `the host never received a request to admit the bot`,
    );
    this.name = "GoogleJoinRefusedError";
    this.evidence = evidence;
    this.guestBlocked = guestBlocked;
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
 * Meet ended a request the host never answered: "No one responded to your request to join the call",
 * or the redirect off Meet that follows it (admission.ts). A lobby timeout on Meet's clock, not ours:
 * on prod every such bot had spent 598-603 s in the lobby of a 900 s budget (3-4 Nov 2026), so
 * knockUntilGoogleAccepts asks again while the budget has room. The message keeps the words join
 * evidence reads as Meet's own expiry ("Google Meet ended the join request").
 */
export class GoogleKnockExpiredError extends AdmissionError {
  readonly why: string;
  constructor(why: string) {
    super("lobby_timeout", `Google Meet ended the join request, nobody admitted the bot: ${why}`);
    this.name = "GoogleKnockExpiredError";
    this.why = why;
  }
}

/** Requests per join that may reach the host, the first included. At Meet's ~10 min a 900 s budget
 *  holds two; the bound keeps a longer budget from asking indefinitely. */
export const GOOGLE_MEET_MAX_REQUESTS = 3;
/** A new request is made only with at least this much of the lobby budget left. */
export const GOOGLE_MEET_REQUEST_MIN_BUDGET_MS = 60_000;
/** … and only after Meet kept the ended request open at least this long. A request Meet ends sooner
 *  is not its 10-minute expiry, and asking again on it could loop. */
export const GOOGLE_MEET_REQUEST_MIN_OPEN_MS = 120_000;

/** One request the host could see: from its knock to the moment it ended, and how it ended. */
interface JoinRequest { from: number; to: number; end: string }

const clockTime = (ms: number) => `${new Date(ms).toISOString().slice(11, 19)}Z`;

/** "2 join requests: 1) 10:00:05Z–10:10:21Z ended by Meet, 2) 10:10:21Z–10:15:05Z lobby budget ran out" */
function describeRequests(requests: JoinRequest[]): string {
  return `${requests.length} join request${requests.length === 1 ? "" : "s"}: ` +
    requests.map((r, i) => `${i + 1}) ${clockTime(r.from)}–${clockTime(r.to)} ${r.end}`).join(", ");
}

function howItEnded(e: AdmissionError): string {
  return e.outcome === "lobby_timeout" ? "lobby budget ran out" : e.outcome === "denial" ? "denied" : e.outcome;
}

/**
 * Run one knock (`knock` = join click + admission wait) and knock again while Meet refuses it, up to
 * GOOGLE_JOIN_MAX_KNOCKS, waiting GOOGLE_JOIN_REKNOCK_BACKOFF_MS before each re-knock and, for an
 * anonymous bot, starting it from a fresh Google state. When Meet ends a request nobody answered, ask
 * again while the budget has room (up to GOOGLE_MEET_MAX_REQUESTS, see the bounds above). Any other
 * outcome — admitted, denied by the host or a captcha, a lobby timeout of our own budget, a crash —
 * ends the loop as it is; after an ended request, a plain AdmissionError's message also names when
 * each request was open, so the join evidence shows how many requests the host saw. `knock` receives
 * the lobby budget still left, so neither the waits nor the re-knocks extend the wait the control
 * plane issued.
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
  const backoff = opts.backoffMs ?? GOOGLE_JOIN_REKNOCK_BACKOFF_MS;
  const maxKnocks = opts.maxKnocks ?? GOOGLE_JOIN_MAX_KNOCKS;
  const deadline = now() + timeoutMs;
  const ended: JoinRequest[] = [];
  let refusals = 0;
  for (;;) {
    resetGoogleJoinRefusal(page);
    const knockedAt = now();
    try {
      return await knock(Math.max(deadline - knockedAt, 0));
    } catch (e) {
      const at = now();
      if (e instanceof GoogleKnockExpiredError) {
        ended.push({ from: knockedAt, to: at, end: "ended by Meet" });
        const left = deadline - at;
        const stop = ended.length >= GOOGLE_MEET_MAX_REQUESTS ? `${ended.length} requests made`
          : left < GOOGLE_MEET_REQUEST_MIN_BUDGET_MS ? `${Math.round(Math.max(left, 0) / 1000)} s of the lobby budget left`
          : at - knockedAt < GOOGLE_MEET_REQUEST_MIN_OPEN_MS ? `Meet ended it after ${Math.round((at - knockedAt) / 1000)} s`
          : null;
        if (stop) throw new AdmissionError("lobby_timeout", `${e.message}; ${describeRequests(ended)}; not asking again: ${stop}`);
        log(`🔁 Google Meet ended join request ${ended.length} unanswered after ${Math.round((at - knockedAt) / 1000)} s (${e.why}); asking again with ${Math.round(left / 1000)} s of the lobby budget left`);
        continue;
      }
      if (!(e instanceof GoogleJoinRefusedError)) {
        // Typed subclasses (a signed-out profile, a page in another language) are checked by type
        // upstream, so only a plain AdmissionError is reworded.
        if (ended.length && e instanceof AdmissionError && e.constructor === AdmissionError) {
          throw new AdmissionError(e.outcome, `${e.message}; ${describeRequests([...ended, { from: knockedAt, to: at, end: howItEnded(e) }])}`);
        }
        throw e;
      }
      refusals++;
      const wait = backoff.length ? backoff[Math.min(refusals - 1, backoff.length - 1)] : 0;
      // A wait that would use up the budget leaves no time to knock, and a meeting that does not
      // admit browser guests refuses every knock: report the refusal now.
      if (e.guestBlocked || refusals >= maxKnocks || deadline - now() <= wait) {
        // The host already saw a request and let it expire: that, not the refusal, is the verdict.
        if (ended.length) {
          throw new AdmissionError("lobby_timeout",
            `Google Meet ended the join request, nobody admitted the bot; ${describeRequests(ended)}; ` +
            `then Meet refused ${refusals} knock${refusals === 1 ? "" : "s"} (${e.evidence})`);
        }
        throw new GoogleJoinRefusedError(e.evidence, refusals);
      }
      log(`🔁 Google Meet refused knock ${refusals} of ${maxKnocks} (${e.evidence}); knocking again${wait ? ` in ${Math.round(wait / 1000)} s` : ""}${opts.freshen ? " as a fresh guest" : ""}`);
      if (wait > 0) await sleep(wait);
      if (opts.freshen) await freshenState(page);
    }
  }
}

import { Page, ElementHandle } from "playwright";
import { log, randomDelay, callJoiningCallback } from "../_host";
import { BotConfig } from "../_host";
import {
  googleNameInputSelectors,
  googleJoinButtonSelectors,
  googleMicrophoneButtonSelectors,
  googleCameraButtonSelectors,
  googleAuthJoinCtaSelectors,
  googleSignedOutLobbyProbeSelectors,
  googleLobbyIconGlyphSelectors,
  googleLobbyCtaMaxLabelChars,
  googleStartupErrorScreenSelectors,
  googleStartupErrorHeadingSelectors,
  googleMeetingNotFoundStartupCodes
} from "./selectors";
import { HumanizedInteractor, MOCAP_LIBRARY } from "./humanized";
import { AdmissionError } from "../shared/admission";
import { resolveBotUiLocale } from "../browser-args";

/** Thrown when authenticated mode detects a signed-out browser profile. Extends AdmissionError so
 *  the JoinDriver's single `instanceof` catch maps the typed `auth_session_missing` outcome to a
 *  PERMANENT completion reason instead of re-raising into a transient (retried) join_failure. */
export class AuthSessionError extends AdmissionError {
  constructor(message: string) {
    super("auth_session_missing", message);
    this.name = "AuthSessionError";
  }
}

/**
 * Signed-out guard probe (authenticated mode): a guest lobby renders a name
 * input; a signed-in lobby never does, in any locale. Structural
 * (jsname/attribute) selectors carry the detection so it cannot fail open on a
 * non-English lobby. A probe error on one selector never breaks the guard —
 * the remaining selectors still get their chance.
 */
export async function isGoogleSignedOutLobby(page: Page): Promise<boolean> {
  for (const sel of googleSignedOutLobbyProbeSelectors) {
    try {
      if (await page.locator(sel).first().isVisible()) return true;
    } catch { /* try the next probe selector */ }
  }
  return false;
}

// Google Meet now blocks browser-synthetic input (Playwright/CDP clicks have
// isTrusted=false and no real pointer movement). "humanized" mode routes join
// interactions through real OS-level XTEST input along recorded-style mouse
// trajectories. Default it on for Google Meet; allow explicit override/opt-out.
export function resolveUiInteractionMode(botConfig: BotConfig): "humanized" | "synthetic" {
  if (botConfig.uiInteractionMode) return botConfig.uiInteractionMode;
  return botConfig.platform === "google_meet" ? "humanized" : "synthetic";
}

/**
 * ── The meeting-does-not-exist screen (#1325) ───────────────────────────────
 *
 * A dead / revoked / mistyped Meet code does not produce a lobby. Meet's
 * call-setup RPC returns 404 ("Requested meeting space does not exist") and the
 * SPA renders an error screen with NO join CTA and NO name input, stamping the
 * failure code on the screen container as `data-startup-code` (217 for this
 * class — the same 217 the bot's console prints from Google's own gRPC body).
 *
 * Before this existed the bot could not see that screen at all. It entered the
 * CTA hunt, burned the full 60s budget, and — because the error screen counts
 * itself down and navigates back to the Meet home page at ~60s — often ended up
 * filling the home page's "Enter a code or link" box before giving up as a
 * generic `join_failure`, which the retry classifier then treats as TRANSIENT
 * and re-spawns against a code that can never exist.
 *
 * The existing `googleRejectionIndicators` copy could never have caught it: that
 * classifier runs inside admission polling, which only begins AFTER a join CTA is
 * clicked, and none of its eight meeting-not-found strings appear on the real
 * page anyway (fixtures/gmeet-404-meeting-not-found.html).
 */

/** What the startup-error screen scan found. `code` is null when no such screen
 *  is present — which is the ordinary case on every lobby. */
export interface MeetStartupError { code: string | null; heading: string; body: string }
export interface MeetStartupErrorOptions { screenSelector: string; headingSelector: string }

/**
 * Locale-agnostic scan for Meet's startup-error screen.
 *
 * RUNS IN BROWSER CONTEXT (page.evaluate serializes this function's source), so
 * — like findLobbyPrimaryCta — it closes over nothing, reads only its argument
 * and `document`, and uses plain CSS. That also makes it directly executable
 * against a jsdom document, which is how meeting-not-found.test.ts pins it
 * against the REAL captured page rather than a mock.
 *
 * The discriminator is the `data-startup-code` attribute, never a word of copy.
 * An empty attribute value is NOT an error screen — the code must be present and
 * non-empty for the screen to be claimed.
 */
export function findMeetStartupError(opts: MeetStartupErrorOptions): MeetStartupError {
  const screen = document.querySelector(opts.screenSelector);
  if (screen === null) return { code: null, heading: "", body: "" };
  const raw = screen.getAttribute("data-startup-code");
  const code = raw === null ? "" : raw.trim();
  if (code.length === 0) return { code: null, heading: "", body: "" };
  const headEl = screen.querySelector(opts.headingSelector);
  const heading = headEl === null ? "" : (headEl.textContent || "").replace(/\s+/g, " ").trim();
  const body = (screen.textContent || "").replace(/\s+/g, " ").trim().slice(0, 300);
  return { code: code, heading: heading, body: body };
}

/** No error screen. The one value this module returns whenever it has not
 *  POSITIVELY recognized a startup-error screen. */
const NO_STARTUP_ERROR: MeetStartupError = { code: null, heading: "", body: "" };

/**
 * Run findMeetStartupError in the page.
 *
 * FAIL-OPEN BY CONSTRUCTION. This guard terminates a join, so it must only ever
 * act on a result it positively recognizes: anything else — an evaluate that
 * threw (navigation mid-flight, closed page), a non-object, a missing or
 * non-string `code` — resolves to "no error screen" and the join proceeds. The
 * cost of a false negative is the pre-existing 60s timeout; the cost of a false
 * positive is refusing a meeting that was joinable.
 */
export async function readMeetStartupError(page: Page): Promise<MeetStartupError> {
  const opts: MeetStartupErrorOptions = {
    screenSelector: googleStartupErrorScreenSelectors.join(", "),
    headingSelector: googleStartupErrorHeadingSelectors.join(", "),
  };
  let r: any;
  try {
    r = await page.evaluate(findMeetStartupError, opts);
  } catch {
    return NO_STARTUP_ERROR;
  }
  if (r === null || typeof r !== "object") return NO_STARTUP_ERROR;
  if (typeof r.code !== "string" || r.code.length === 0) return NO_STARTUP_ERROR;
  return {
    code: r.code,
    heading: typeof r.heading === "string" ? r.heading : "",
    body: typeof r.body === "string" ? r.body : "",
  };
}

/**
 * Typed error for a startup-error screen. `meeting_not_found` ONLY for codes we
 * have captured AND corroborated in production (googleMeetingNotFoundStartupCodes);
 * every other code still terminates the join fast — the screen carries no CTA, so
 * waiting is provably pointless — but is reported as a plain `join_failure`,
 * because naming a class we have not evidenced is how the copy list above got
 * written.
 */
export function startupErrorToAdmissionError(e: MeetStartupError, url: string): AdmissionError {
  const code = e.code === null ? "?" : e.code;
  const said = e.heading.length > 0 ? ` — Meet says: "${e.heading}"` : "";
  if (e.code !== null && googleMeetingNotFoundStartupCodes.indexOf(e.code) >= 0) {
    return new AdmissionError(
      "meeting_not_found",
      `Google Meet has no such meeting space (startup code ${code})${said}. ` +
        `The link is dead, revoked or mistyped — there is no lobby to join and a retry cannot help. url=${url}`,
    );
  }
  return new AdmissionError(
    "join_failure",
    `Google Meet rendered a startup-error screen (startup code ${code})${said}, which carries no join control. url=${url}`,
  );
}

/**
 * THROW if the page is a Meet startup-error screen. Called (a) once immediately
 * after navigation, before any CTA or name-input hunting begins — the "pre-CTA"
 * position that is the whole point of #1325 — and (b) on the poll cadence inside
 * the selector waits, so a screen that arrives late is still caught in seconds
 * rather than at the end of a 60s or 120s budget.
 *
 * It runs AHEAD of the ordered selector list inside those waits, deliberately.
 * On the captured error screen the list's broad locale-agnostic backstop
 * (`button[jsname]:not([aria-label]):has(span)`) matches "Return to home screen"
 * — the FIRST such button on the page — so a list-first order does not merely
 * waste the budget, it clicks a control that navigates away from the meeting.
 * The guard is cheap (one querySelector) and returns immediately on any page
 * without a startup-error screen, which is every lobby.
 */
export async function guardMeetStartupError(page: Page): Promise<void> {
  const err = await readMeetStartupError(page);
  if (err.code === null) return;
  let url = "?";
  try { url = page.url(); } catch { /* best-effort */ }
  try {
    await page.screenshot({ path: "/app/storage/screenshots/bot-checkpoint-startup-error.png", fullPage: true });
  } catch { /* best-effort */ }
  log(`📸 Screenshot: Meet startup-error screen (data-startup-code=${err.code}) — "${err.heading}"`);
  throw startupErrorToAdmissionError(err, url);
}

/** Poll cadence for the ordered selector resolvers. */
const SELECTOR_POLL_MS = 300;
/** The structural CTA scan runs every Nth poll — it is a full-document walk. */
const CTA_SCAN_EVERY_POLLS = 5;
/** The startup-error probe (#1325) runs every Nth poll. Cheap (one querySelector),
 *  but there is no reason to run it 3× a second. */
const STARTUP_ERROR_EVERY_POLLS = 4;
/** The scan only starts once the lobby SPA has had time to finish rendering:
 *  a half-built lobby can momentarily expose exactly one text button that is
 *  not the CTA, and the scan's whole safety argument is uniqueness. */
const CTA_SCAN_GRACE_MS = 8000;
/** Origin tag for the structural scan. DIAGNOSTIC-ONLY: the scan never clicks or
 *  returns a handle to the join flow (owner ruling on #856/#917). It is retained
 *  purely to (a) record candidate labels for the failure diagnostic and (b) emit
 *  a telemetry line when it WOULD have uniquely resolved a lobby the selectors
 *  could not — the evidence a future re-promotion would need. */
export const STRUCTURAL_CTA_ORIGIN = "structural:lobby-primary-cta";

/** Result of the browser-context lobby scan. `el` is non-null ONLY when exactly
 *  one candidate passed — see findLobbyPrimaryCta. `labels` is every candidate's
 *  visible text, kept for the failure diagnostic. */
export interface LobbyCtaScan { el: Element | null; labels: string[] }
export interface LobbyCtaScanOptions { iconGlyphSelector: string; maxLabelChars: number }

/**
 * Locale-agnostic primary-CTA scan for the Google Meet lobby.
 *
 * RUNS IN BROWSER CONTEXT (page.evaluateHandle serializes this function's
 * source), so it is self-contained by construction: it closes over nothing,
 * reads only its argument and `document`, and uses plain CSS. That also makes it
 * directly executable against a jsdom document, which is how join-cta.test.ts
 * pins it against a real DOM rather than a mock.
 *
 * The discriminator is positive and structural, never textual: the lobby's
 * primary CTA is the one visible, enabled <button> that carries a real text
 * label and no icon glyph. Everything else in the lobby is an icon affordance
 * (mic / camera / 3-dot menu) or pairs an icon with its text ("cast this
 * meeting", "use a phone for audio"), so `iconGlyphSelector` removes them
 * without knowing a single word of the UI language.
 *
 * WHY IT CANNOT MIS-CLICK: it returns an element ONLY when exactly one button in
 * the document passes. A second text-labelled button — a consent dialog, a
 * "cancel", an unrecognized icon rendering as ligature text — makes the result
 * ambiguous, and an ambiguous result resolves nothing and clicks nothing. The
 * caller then fails loud with every candidate label recorded. Over-inclusion
 * degrades to a diagnosable timeout; it never degrades to the wrong control.
 */
export function findLobbyPrimaryCta(opts: LobbyCtaScanOptions): LobbyCtaScan {
  const labels: string[] = [];
  const candidates: Element[] = [];
  const buttons = document.querySelectorAll("button");
  for (let i = 0; i < buttons.length; i++) {
    const btn = buttons[i] as HTMLButtonElement;
    if (btn.disabled || btn.getAttribute("aria-disabled") === "true") continue;
    if (btn.closest("[hidden]") !== null) continue;
    const rect = btn.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const style = btn.ownerDocument.defaultView.getComputedStyle(btn);
    if (style.visibility === "hidden" || style.display === "none" || style.opacity === "0") continue;
    // Icon affordance, in any language.
    if (btn.querySelector(opts.iconGlyphSelector) !== null) continue;
    const text = (btn.textContent || "").replace(/\s+/g, " ").trim();
    if (text.length === 0 || text.length > opts.maxLabelChars) continue;
    // A Material ligature that escaped the icon filter ("mic_off", "more_vert")
    // is not a label: no natural-language CTA contains an underscore.
    if (text.indexOf("_") >= 0) continue;
    // Must contain an actual letter — a glyph/number-only button is not a CTA.
    if (!/\p{L}/u.test(text)) continue;
    labels.push(text);
    candidates.push(btn);
  }
  return { el: candidates.length === 1 ? candidates[0] : null, labels: labels };
}

/** Run findLobbyPrimaryCta in the page and lift the winner into an ElementHandle. */
async function scanLobbyPrimaryCta(
  page: Page
): Promise<{ handle: ElementHandle<Element> | null; labels: string[] }> {
  const opts: LobbyCtaScanOptions = {
    iconGlyphSelector: googleLobbyIconGlyphSelectors.join(", "),
    maxLabelChars: googleLobbyCtaMaxLabelChars,
  };
  let scan: any = null;
  try {
    scan = await page.evaluateHandle(findLobbyPrimaryCta, opts);
    const labels = (await (await scan.getProperty("labels")).jsonValue()) as string[];
    const handle = (await scan.getProperty("el")).asElement();
    return { handle: (handle as ElementHandle<Element>) || null, labels: labels || [] };
  } catch {
    return { handle: null, labels: [] };
  } finally {
    if (scan) { try { await scan.dispose(); } catch { /* best-effort */ } }
  }
}

/**
 * First VISIBLE selector in list order, or null. Order is authoritative: the
 * whole list is re-checked top-down on every poll, so the locale-agnostic entry
 * can never be beaten to the punch by a broader English fallback (or the
 * reverse). A per-selector parse/detach rejection never denies the rest their
 * turn.
 */
async function firstVisibleSelector(
  page: Page,
  selectors: string[]
): Promise<{ handle: ElementHandle<Element>; selector: string } | null> {
  for (const sel of selectors) {
    try {
      const loc = page.locator(sel).first();
      if (!(await loc.isVisible())) continue;
      const handle = await loc.elementHandle({ timeout: 2000 });
      if (handle) return { handle: handle as ElementHandle<Element>, selector: sel };
    } catch { /* invalid selector or detached node — the next entry still gets its chance */ }
  }
  return null;
}

/**
 * Observed page context for a selector miss. Recorded INTO the thrown error so
 * the failure is diagnosable from `meeting.data.last_error` alone — the pods
 * that saw the lobby are long gone by the time anyone reads it (#846 A4).
 */
async function observedPageContext(page: Page): Promise<string> {
  let url = "?";
  try { url = page.url(); } catch { /* best-effort */ }
  try {
    const ctx: any = await page.evaluate(() => ({
      lang: document.documentElement.getAttribute("lang") || "",
      nav: navigator.language || "",
    }));
    return `url=${url} html.lang=${ctx.lang || "?"} navigator.language=${ctx.nav || "?"}`;
  } catch {
    return `url=${url} html.lang=? navigator.language=?`;
  }
}

/**
 * Screenshot + compose the LOUD failure message for a total selector miss
 * (no-fallbacks.md — a missing control fails with a logged reason + screenshot,
 * never a silent skip). The message keeps its historical prefix verbatim (prod
 * monitoring greps it) and appends the observed locale/URL, plus the visible
 * text-button labels when a structural scan ran — the one datum that turns the
 * next occurrence into a one-look diagnosis.
 */
export async function describeSelectorMiss(
  page: Page,
  selectors: string[],
  timeoutMs: number,
  label: string,
  candidateLabels: string[] | null
): Promise<string> {
  const shot = `/app/storage/screenshots/bot-checkpoint-${label.replace(/[^a-z0-9]+/gi, "-")}-not-found.png`;
  try { await page.screenshot({ path: shot, fullPage: true }); } catch { /* best-effort */ }
  log(`📸 Screenshot: ${label} not found by any of ${selectors.length} selectors (tried: ${selectors.join(" | ")})`);
  const context = await observedPageContext(page);
  const seen = candidateLabels === null
    ? ""
    : `; visible text buttons: ${candidateLabels.length === 0 ? "(none)" : candidateLabels.map((t) => `"${t}"`).join(" | ")}`;
  return `Could not locate ${label} by any locale-agnostic or English selector after ${timeoutMs}ms (${context}${seen})`;
}

/**
 * Wait for the FIRST of an ordered selector list to become visible
 * (locale-agnostic selectors first, English text fallbacks last). Returns the
 * matched handle and the selector that won. On total failure: screenshot + LOUD
 * throw carrying the observed locale/URL.
 */
export async function waitForAnySelector(
  page: Page,
  selectors: string[],
  timeoutMs: number,
  label: string
): Promise<{ handle: ElementHandle<Element>; selector: string }> {
  const started = Date.now();
  let polls = 0;
  do {
    // #1325 FIRST, not last: on the real error screen the list's broad structural
    // backstop matches "Return to home screen", so a list-first order would click
    // the wrong control and navigate off the meeting.
    if (polls % STARTUP_ERROR_EVERY_POLLS === 0) await guardMeetStartupError(page);
    const hit = await firstVisibleSelector(page, selectors);
    if (hit) {
      log(`Located ${label} via selector: ${hit.selector}`);
      return hit;
    }
    polls++;
    await page.waitForTimeout(SELECTOR_POLL_MS);
  } while (Date.now() - started < timeoutMs);

  throw new Error(await describeSelectorMiss(page, selectors, timeoutMs, label, null));
}

/**
 * Resolve the Meet lobby's primary admission CTA by the ordered selector list.
 *
 * The structural scan (findLobbyPrimaryCta) still runs on its cadence, but it is
 * DIAGNOSTIC-ONLY (owner ruling on #856/#917): it NEVER returns a handle and the
 * join flow never clicks its element. Two reasons it was demoted from backstop to
 * diagnostic: (1) its uniqueness guard protects against ambiguity but not against
 * a unique-but-WRONG button, and it was only ever proven on fabricated jsdom
 * fixtures; (2) the real fix for the non-English lobby is #856 — the browser UI
 * locale is now pinned, so Meet renders English by construction and the exact
 * selectors above resolve it deterministically.
 *
 * What the scan is kept for: (a) its candidate labels feed the failure diagnostic
 * (so a total miss still records every visible text button), and (b) when it WOULD
 * have uniquely resolved a lobby that the selector list could not, it logs a
 * telemetry line — that is the only evidence a future re-promotion could stand on
 * (does a real non-English lobby exist where the scan uniquely wins?). It does not
 * shorten or lengthen the selector budget.
 */
export async function waitForLobbyCta(
  page: Page,
  selectors: string[],
  timeoutMs: number,
  label: string
): Promise<{ handle: ElementHandle<Element>; selector: string }> {
  const started = Date.now();
  const graceMs = Math.min(CTA_SCAN_GRACE_MS, Math.floor(timeoutMs / 3));
  let polls = 0;
  // null until the scan has actually run: "(none)" must mean "the lobby showed
  // no text-labelled button", never "the scan never got to look".
  let lastLabels: string[] | null = null;
  do {
    // #1325 FIRST, not last — see waitForAnySelector. The error screen's
    // "Return to home screen" button satisfies the list's broad structural
    // backstop (`button[jsname]:not([aria-label]):has(span)`) and sits ahead of
    // "Submit feedback" in DOM order, so on a dead meeting code a list-first
    // order resolves it AS THE JOIN CTA and clicks it.
    if (polls % STARTUP_ERROR_EVERY_POLLS === 0) await guardMeetStartupError(page);
    const hit = await firstVisibleSelector(page, selectors);
    if (hit) {
      log(`Located ${label} via selector: ${hit.selector}`);
      return hit;
    }
    if (Date.now() - started >= graceMs && polls % CTA_SCAN_EVERY_POLLS === 0) {
      const scan = await scanLobbyPrimaryCta(page);
      lastLabels = scan.labels;
      // Diagnostic-only: the scan is NOT allowed to resolve the CTA. If it would
      // have uniquely picked a button the selector list has not matched this poll,
      // record that as telemetry — prod evidence for whether a real non-English
      // lobby exists where re-promoting the scan would help. We do NOT click it,
      // and we dispose the handle so nothing downstream can.
      if (scan.handle) {
        log(`structural scan candidate (diagnostic-only): "${scan.labels[0]}"`);
        try { await scan.handle.dispose(); } catch { /* best-effort */ }
      }
    }
    polls++;
    await page.waitForTimeout(SELECTOR_POLL_MS);
  } while (Date.now() - started < timeoutMs);

  throw new Error(await describeSelectorMiss(page, selectors, timeoutMs, label, lastLabels));
}

/**
 * Append `?hl=<lang>` to the Meet URL so the lobby renders in the pinned UI
 * language (#856). This is the lever that matters specifically on the
 * BOT_AUTHENTICATED path: a signed-in Google account's own language preference
 * otherwise wins over the browser's --lang/context locale. `hl` takes the bare
 * language subtag (en-US → en). An existing `hl` on the caller's URL is
 * preserved (never overridden). A malformed URL is returned unchanged.
 */
export function withPinnedMeetLocale(meetingUrl: string, locale: string): string {
  const hl = (locale.split("-")[0] || locale).trim();
  if (!hl) return meetingUrl;
  try {
    const u = new URL(meetingUrl);
    if (!u.searchParams.has("hl")) u.searchParams.set("hl", hl);
    return u.toString();
  } catch {
    return meetingUrl;
  }
}

export async function joinGoogleMeeting(
  page: Page,
  meetingUrl: string,
  botName: string,
  botConfig: BotConfig
): Promise<void> {
  const navUrl = withPinnedMeetLocale(meetingUrl, resolveBotUiLocale());
  await page.goto(navUrl, { waitUntil: "domcontentloaded" });
  await page.bringToFront();

  // Take screenshot after navigation
  await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-after-navigation.png', fullPage: true });
  log("📸 Screenshot taken: After navigation to meeting URL");

  // --- Call joining callback to notify meeting-api that bot is joining ---
  // Fix 2: Propagate JOINING callback failure — bot must NOT proceed if server rejected
  await callJoiningCallback(botConfig);
  log("Joining callback sent successfully");

  // Brief wait for page elements to settle (networkidle already ensures page loaded)
  await page.waitForTimeout(1000);

  // PRE-CTA (#1325): if Meet answered "no such meeting space", say so NOW. This
  // runs before the humanized input layer, before the name input and before any
  // CTA hunting — on a dead code the old path burned 60s looking for a button
  // that a 404 screen never renders, then reported a generic join_failure the
  // control plane retried.
  await guardMeetStartupError(page);

  // Record the resolved UI locale on the SUCCESS path too (#856): the locale used
  // to be invisible until a failure. observedPageContext reads navigator.language
  // and <html lang> — with the pin in place these should read en-US / en.
  log(`Lobby locale (#856): ${await observedPageContext(page)}`);

  // --- Humanized input layer (defeats Google Meet input-authenticity detection) ---
  const uiMode = resolveUiInteractionMode(botConfig);
  let humanizer: HumanizedInteractor | null = null;
  if (uiMode === "humanized") {
    humanizer = new HumanizedInteractor(MOCAP_LIBRARY, {
      log,
      onMissScreenshot: async (p, reason) => {
        await p.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-humanized-click-miss.png', fullPage: true });
        log(`📸 Screenshot: humanized click abandoned as off-target — ${reason}`);
      },
    });
    if (!(await humanizer.available())) {
      log("WARNING: humanized UI mode requested but xdotool/X display is unavailable — falling back to synthetic input. Install xdotool+xclip in the bot image.");
      humanizer = null;
    } else {
      log("Humanized UI interaction mode active (OS-level XTEST input).");
    }
  }

  // Click a resolved element handle via humanized motion, falling back to a
  // synthetic handle click if humanized interaction is off or errors.
  const clickHandle = async (handle: ElementHandle<Element>, label: string): Promise<void> => {
    if (humanizer) {
      try {
        await humanizer.navigateAndClick(page, handle);
        return;
      } catch (e) {
        log(`Humanized click failed for '${label}' (${e}); falling back to synthetic click.`);
      }
    }
    await handle.click();
  };

  // Fill a text field via humanized click+paste, falling back to page.fill.
  const fillField = async (
    handle: ElementHandle<Element>,
    selector: string,
    text: string,
    label: string
  ): Promise<void> => {
    if (humanizer) {
      try {
        await humanizer.fillField(page, handle, text);
        return;
      } catch (e) {
        log(`Humanized fill failed for '${label}' (${e}); falling back to page.fill.`);
      }
    }
    await page.fill(selector, text);
  };

  if (botConfig.authenticated) {
    // Authenticated flow: browser is logged into Google, skip name input
    log("Authenticated mode: skipping name input (using Google account identity)");

    // Wait for the lobby to fully load (SPA needs time after domcontentloaded)
    log("Waiting for lobby to load...");
    await page.waitForTimeout(5000);

    // Diagnostic screenshot to see what the lobby shows
    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-auth-lobby.png', fullPage: true });
    log("📸 Diagnostic screenshot: auth lobby state");

    // Mute mic and camera if visible
    try {
      const micHandle = await page.waitForSelector(googleMicrophoneButtonSelectors[0], { timeout: 3000 });
      if (micHandle) { await clickHandle(micHandle, "microphone"); log("Microphone muted."); }
    } catch (e) {
      log("Microphone already muted or not found.");
    }

    try {
      const cameraHandle = await page.waitForSelector(googleCameraButtonSelectors[0], { timeout: 3000 });
      if (cameraHandle) { await clickHandle(cameraHandle, "camera"); log("Camera turned off."); }
    } catch (e) {
      log("Camera already off or not found.");
    }

    // Authenticated lobby: one primary CTA — "Join now" (standard join),
    // "Switch here" (same account already in the call) or "Ask to join"
    // (host approval required) — or any localized equivalent. The CTA is
    // located by the ordered selector list (googleAuthJoinCtaSelectors, exact
    // text first now the UI locale is pinned — #856). The structural scan runs
    // diagnostic-only and never clicks; waitForLobbyCta fails LOUD (screenshot +
    // selector list + observed locale) if no CTA appears.
    const { handle: ctaHandle, selector: ctaSelector } = await waitForLobbyCta(
      page,
      googleAuthJoinCtaSelectors,
      30000,
      "authenticated join CTA"
    );

    // Signed-out guard: a guest lobby (name input rendered) means the persisted
    // browser profile is signed out — fail closed with a typed error instead of
    // silently joining as an anonymous guest. The probe is structural, so the
    // guard holds on non-English lobbies too. A signed-in account that is
    // merely not pre-admitted shows no name input and proceeds to knock.
    if (await isGoogleSignedOutLobby(page)) {
      await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-auth-signed-out.png', fullPage: true });
      log("📸 Screenshot: authenticated mode but browser profile is signed out (guest lobby).");
      throw new AuthSessionError(
        "Browser profile signed out — cannot authenticate with Google. Re-authenticate the profile and retry."
      );
    }

    await clickHandle(ctaHandle, "authenticated_join");
    log(`Bot clicked the authenticated join CTA (via ${ctaSelector}).`);

    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-after-join-now.png', fullPage: true });
    log("📸 Screenshot taken: After join click (authenticated)");
  } else {
    // Anonymous flow: enter bot name and ask to join
    log("Attempting to find name input field...");

    const { handle: nameHandle, selector: nameFieldSelector } = await waitForAnySelector(
      page,
      googleNameInputSelectors,
      120000,
      "name input"
    );
    log("Name input field found.");

    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-name-field-found.png', fullPage: true });

    await fillField(nameHandle!, nameFieldSelector, botName, "name");

    // Mute mic and camera if available
    try {
      const micHandle = await page.waitForSelector(googleMicrophoneButtonSelectors[0], { timeout: 1000 });
      if (micHandle) await clickHandle(micHandle, "microphone");
    } catch (e) {
      log("Microphone already muted or not found.");
    }

    try {
      const cameraHandle = await page.waitForSelector(googleCameraButtonSelectors[0], { timeout: 1000 });
      if (cameraHandle) await clickHandle(cameraHandle, "camera");
    } catch (e) {
      log("Camera already off or not found.");
    }

    const { handle: joinHandle } = await waitForLobbyCta(
      page,
      googleJoinButtonSelectors,
      60000,
      "join button"
    );
    await clickHandle(joinHandle!, "ask_to_join");
    log(`${botName} joined the Google Meet Meeting.`);

    await page.screenshot({ path: '/app/storage/screenshots/bot-checkpoint-0-after-ask-to-join.png', fullPage: true });
    log("📸 Screenshot taken: After clicking 'Ask to join'");
  }
}

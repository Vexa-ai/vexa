/**
 * Zoom auth-session guard (#1061) — name the SIGNED-OUT BOT PROFILE.
 *
 * THE PRODUCTION DEFECT: on hosted prod (since 2026-07-25) every `auth_session_missing` in the
 * release is Zoom — 4 of 23 Zoom meetings, and no other platform produces the reason at all. The
 * reason is PERMANENT by design (`lifecycle/retry.py`: "signed-out profile — a re-spawn hits the
 * same dead profile"), so the state does not heal: every meeting routed to that profile burns.
 *
 * THE GAP THIS FILE CLOSES: today the join path detects a Zoom sign-in wall only in GUEST mode,
 * where it means "the host restricted entry to authenticated users" (a meeting-policy fact, not our
 * fault). In AUTHENTICATED mode — the mode that actually owns a profile that can die — the same
 * wall was logged and walked past ("proceeding (the persistent context should already carry a Zoom
 * session)"). The join then failed minutes later as a nameless join-button timeout, so the one
 * state that says "our credential is dead, page someone" was the one state nothing surfaced.
 *
 * SHAPE: a pure classifier over an observation record, so the decision is unit-testable with no
 * browser (session.test.ts), plus a thin reader that fills the record off a live Page. Mirrors
 * googlemeet/join.ts's `isGoogleSignedOutLobby` + `AuthSessionError` — same typed outcome, same
 * PERMANENT mapping through the JoinDriver, one guard per platform.
 *
 * SCOPE: DETECTION ONLY. Re-authenticating the dead profile (the self-heal) is deliberately out of
 * scope and tracked as follow-up work — this file's whole job is to make the state say its name.
 *
 * FIXTURE HONESTY: the signals below are derived from the SHIPPED join path's own account of
 * signed-in vs guest Zoom pre-join, from @vexa/remote-browser's session validator (`validate.ts`:
 * sign-in URL markers + the `zm_aid` account cookie, with `_zm_ssid` explicitly rejected as a
 * logged-in signal because Zoom sets it the instant the sign-in page loads), and from the issue's
 * production evidence. They have NOT been replayed against a live dead Zoom profile.
 */
import type { Page } from "playwright";
import { AdmissionError } from "../shared/admission";
import { log } from "../_host";
import { zoomNameInputSelector, zoomSignInWallTexts, zoomSignInUrlMarkers } from "./selectors";

/**
 * The cookie only a REAL Zoom ACCOUNT session carries. `_zm_ssid` is deliberately NOT here: Zoom
 * sets it on the anonymous sign-in page too, so its presence proves nothing (the same false-positive
 * @vexa/remote-browser's login flow already learned the hard way).
 */
export const ZOOM_ACCOUNT_COOKIE_NAME = "zm_aid";

/** Which observation carried the verdict. Recorded in the raw detail so a triage can grep it. */
export type ZoomSignedOutSignal = "signin_redirect" | "signin_wall" | "guest_lobby";

/** The short machine-greppable tag that leads the failure reason text. */
export const ZOOM_AUTH_SESSION_MISSING = "zoom_auth_session_missing";

/**
 * Thrown when AUTHENTICATED mode finds a signed-out Zoom profile. Extends AdmissionError so the
 * JoinDriver's single `instanceof` catch maps the typed `auth_session_missing` outcome onto the
 * PERMANENT completion reason, instead of re-raising into a transient (re-spawned) join_failure.
 * The `detail` also rides the Error `message`, which is what the driver carries into the terminal
 * lifecycle row's reason text (#926) — the only evidence channel available on main today.
 */
export class ZoomAuthSessionError extends AdmissionError {
  readonly signal: ZoomSignedOutSignal;
  readonly detail: string;
  constructor(signal: ZoomSignedOutSignal, detail: string) {
    super("auth_session_missing", `[Zoom Web] ${ZOOM_AUTH_SESSION_MISSING}: ${detail}`);
    this.name = "ZoomAuthSessionError";
    this.signal = signal;
    this.detail = detail;
  }
}

/** What the classifier reads off the pre-join page. Plain data — no Page, no DOM, no I/O. */
export interface ZoomSessionObservation {
  /** page.url() at observation time. */
  url: string;
  /** The matched sign-in-wall phrase, or null when no wall text is on the page. */
  signInWallText: string | null;
  /** Did the guest name-entry field render at all? */
  nameFieldPresent: boolean;
  /** Its current value — '' when it rendered empty. */
  nameFieldValue: string;
  /** true / false when the cookie jar was readable; null when it was not (never a verdict). */
  accountCookiePresent: boolean | null;
  /** Where in the join path this was taken — carried into the detail for triage. */
  phase: string;
}

export type ZoomSessionVerdict =
  | { signedOut: false }
  | { signedOut: true; signal: ZoomSignedOutSignal; detail: string };

/** Drop the query string — a Zoom join URL carries `?pwd=` and a meeting passcode must not be logged. */
export function redactZoomUrl(raw: string): string {
  if (!raw) return "";
  try {
    const u = new URL(raw);
    return `${u.origin}${u.pathname}`;
  } catch {
    return raw.split("?")[0];
  }
}

/** Is this a canonical-Zoom sign-in / login URL? Canonical hosts only — see zoomSignInUrlMarkers. */
export function isZoomSignInUrl(raw: string): boolean {
  try {
    const u = new URL(raw);
    const canonical = u.hostname === "zoom.us" || u.hostname.endsWith(".zoom.us");
    if (!canonical) return false;
    const path = u.pathname.toLowerCase();
    return zoomSignInUrlMarkers.some((m) => path.includes(m));
  } catch {
    return false;
  }
}

/**
 * The verdict. Called ONLY in authenticated mode — in guest mode a sign-in prompt is the host's
 * policy, not a dead credential, and the existing guest-mode branch already reports that.
 *
 * Ordered by strength, and every rule is written to fail OPEN on ambiguity: a false positive
 * refuses a legitimate join with a PERMANENT, un-retried reason, which is strictly worse than the
 * status quo (a nameless timeout that at least retries).
 *
 *  1. `signin_redirect` — the canonical Zoom web client bounced us to zoom.us/signin. A live
 *     session is never bounced. Decisive on its own.
 *  2. `signin_wall`     — Zoom rendered "sign in to join …" while we claim to hold a session. A
 *     signed-in client is not asked to sign in. Decisive on its own; this is exactly the state the
 *     old code logged and walked past.
 *  3. `guest_lobby`     — the guest name-entry field rendered EMPTY *and* the account cookie is
 *     provably absent. Neither half is decisive alone: the shipped join path already tolerates a
 *     signed-in pre-join whose name field comes up empty ("typed fallback"), and keying on a cookie
 *     name alone would blanket-refuse every authenticated Zoom join the day Zoom renames it. An
 *     unreadable cookie jar (null) is not "absent" and never convicts.
 */
export function classifyZoomSession(obs: ZoomSessionObservation): ZoomSessionVerdict {
  const where = `url=${redactZoomUrl(obs.url)} phase=${obs.phase}`;

  if (isZoomSignInUrl(obs.url)) {
    return { signedOut: true, signal: "signin_redirect", detail: `signin_redirect ${where}` };
  }
  if (obs.signInWallText) {
    return {
      signedOut: true,
      signal: "signin_wall",
      detail: `signin_wall matched="${obs.signInWallText}" ${where}`,
    };
  }
  if (obs.nameFieldPresent && obs.nameFieldValue.trim() === "" && obs.accountCookiePresent === false) {
    return {
      signedOut: true,
      signal: "guest_lobby",
      detail: `guest_lobby empty_name_field no_${ZOOM_ACCOUNT_COOKIE_NAME}_cookie ${where}`,
    };
  }
  return { signedOut: false };
}

/** Read the classifier's inputs off a live page. Every probe is individually best-effort: one
 *  unreadable signal must not blind the others (and unreadable never convicts — see classify). */
export async function observeZoomSession(page: Page, phase: string): Promise<ZoomSessionObservation> {
  let url = "";
  try { url = page.url(); } catch { /* navigating — leave blank */ }

  const signInWallText = await page
    .evaluate((phrases: string[]) => {
      const body = (document.body?.innerText || "").toLowerCase();
      for (const p of phrases) if (body.includes(p.toLowerCase())) return p;
      return null;
    }, zoomSignInWallTexts)
    .catch(() => null);

  const nameField = page.locator(zoomNameInputSelector).first();
  const nameFieldPresent = await nameField.isVisible({ timeout: 1000 }).catch(() => false);
  const nameFieldValue = nameFieldPresent ? await nameField.inputValue().catch(() => "") : "";

  let accountCookiePresent: boolean | null = null;
  try {
    const cookies = (await page.context().cookies()) as Array<{ name: string; value: string }>;
    accountCookiePresent = cookies.some((c) => c.name === ZOOM_ACCOUNT_COOKIE_NAME && !!c.value);
  } catch { /* no readable jar (a non-persistent stand-in context) — stays null, never a verdict */ }

  return { url, signInWallText, nameFieldPresent, nameFieldValue, accountCookiePresent, phase };
}

/**
 * The guard the join path calls in authenticated mode: observe, classify, and THROW the typed
 * PERMANENT failure when the profile is signed out. A not-signed-out verdict returns quietly, so
 * the join proceeds exactly as it does today.
 */
export async function assertZoomAuthSession(page: Page, phase: string): Promise<void> {
  const verdict = classifyZoomSession(await observeZoomSession(page, phase));
  if (!verdict.signedOut) return;
  log(
    `[Zoom Web] ⛔ ${ZOOM_AUTH_SESSION_MISSING}: the bot's Zoom profile is SIGNED OUT — ${verdict.detail}. ` +
    `PERMANENT: a re-spawn restores the same dead profile, so every meeting routed to it fails until ` +
    `the profile is re-authenticated (\`make login\` / provision-cli, AUTH_PLATFORM=zoom).`,
  );
  throw new ZoomAuthSessionError(verdict.signal, verdict.detail);
}

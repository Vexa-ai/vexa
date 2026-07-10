// Jitsi Meet (web client) selectors — the JOIN layer's read surface
// (join / admission / leave / removal). Speaker-tile and capture selectors are
// recording concerns and stay OUTSIDE this brick.
//
// Jitsi Meet is self-hostable, so the DOM can vary by deployment version.
// Every lookup below therefore carries fallbacks ordered newest-UI-first, and
// the admission oracle prefers the `window.APP.conference` runtime API (stable
// across versions, part of the jitsi-meet web app) over DOM heuristics.
//
// TEXT-SELECTOR SEMANTICS (Playwright): quoted `text="foo"` is EXACT match
// (case-sensitive); unquoted `text=foo` is SUBSTRING (case-insensitive).
// `*Texts` exports are raw strings scanned inside page.evaluate() — not
// Playwright selectors. src/shared/selector-validity.test.ts gates the
// `*Selectors` / `*Indicators` arrays.

// ---- Pre-join page ----

// Display-name input on the prejoin screen. `#premeeting-name-input` is the
// stable id in current jitsi-meet; the class + placeholder forms cover older
// deployments.
export const jitsiNameInputSelector =
  '#premeeting-name-input, .prejoin-input-area input, input[placeholder*="name" i]';

// The prejoin "Join meeting" button. `data-testid` is current jitsi-meet;
// the class form covers older builds.
export const jitsiJoinButtonSelector =
  '[data-testid="prejoin.joinMeeting"], .prejoin-preview-join-btn, [aria-label="Join meeting"]';

// A container that only renders on the prejoin screen (used to distinguish
// "still pre-join" from "in conference" in the admission oracle).
export const jitsiPrejoinScreenSelectors: string[] = [
  '#premeeting-name-input',
  '[data-testid="prejoin.joinMeeting"]',
  '.premeeting-screen',
  '.prejoin-input-area',
];

// ---- Password-protected rooms ----
// The password prompt renders as a dialog with a password input. Generic
// type/placeholder matches cover the dialog across versions.
export const jitsiPasswordInputSelector =
  'input[type="password"], input[placeholder*="password" i], input[name="lockKey"]';

// ---- In-meeting admission indicators ----

// The hangup (leave) control only renders inside the conference — the primary
// DOM positive. aria-label falls back for builds without the class.
export const jitsiHangupButtonSelectors: string[] = [
  '.hangup-button',
  '[data-testid="toolbox.hangup"]',
  '[aria-label="Leave the meeting"]',
  '[aria-label*="hang up" i]',
];

// The conference stage — present once the app enters the room (also while in
// lobby on some builds, so never sufficient alone; the oracle pairs it with
// the absence of prejoin/lobby indicators).
export const jitsiConferenceIndicators: string[] = [
  '#largeVideoContainer',
  '#largeVideoWrapper',
  '.toolbox-content-items',
];

// ---- Lobby (knocking) / waiting indicators ----
// With lobby enabled, the app shows a "knocking" screen after the join click
// until a moderator admits or declines. Members-only rooms show a
// waiting-for-host dialog instead.
export const jitsiLobbyIndicators: string[] = [
  '[data-testid="lobby.joiningMessage"]',
  '.lobby-screen',
  'text=Asking to join',
  'text=Waiting for a moderator',
  'text=Waiting for the host',
];
export const jitsiLobbyTexts = [
  'Asking to join',
  'Waiting for a moderator',
  'Waiting for the host',
  'conference has not yet started',
];

// ---- Rejection / removal / end-of-meeting text (page.evaluate scans) ----
// Lobby decline, kick, and termination all surface as dialog/notification text.
export const jitsiRejectionTexts = [
  'Your request to join has been declined',
  'request to join has been rejected',
  'declined by a moderator',
];
export const jitsiRemovalTexts = [
  'kicked out of the meeting',
  'You have been kicked',
  'The meeting has been terminated',
  'meeting has been terminated',
  'You have been disconnected',
  'The conference has ended',
];

// ---- Post-meeting / rejoin page indicators ----
// After a leave/kick jitsi lands on a feedback / "Rejoin" page (or the
// deployment's close page).
export const jitsiPostMeetingIndicators: string[] = [
  'text=Rejoin',
  'text=Thank you for using',
];

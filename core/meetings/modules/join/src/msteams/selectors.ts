// Centralized MS Teams selectors and indicators for the JOIN layer
// (join / admission / leave / removal). Speaker-detection and caption
// selectors are recording concerns and stay OUTSIDE this brick.
// Keep this file free of runtime logic; export constants only.
//
// TEXT-SELECTOR SEMANTICS (Playwright): quoted `text="foo"` is EXACT match
// (case-sensitive); unquoted `text=foo` is SUBSTRING match (case-insensitive,
// whitespace-normalized). `text*=` is NOT a Playwright engine — such entries
// threw InvalidSelectorError on every locator call and were silently skipped
// by the detectors' try/catch loops (dead selectors). All `text*="foo"`
// entries were replaced with the unquoted substring form on 2026-07-04;
// src/shared/selector-validity.test.ts now gates every selector array.

// Browser-context button matcher (shared shape) — see ../shared/leave-click.
import type { BrowserContextButtonMatcher } from "../shared/leave-click";
export type { BrowserContextButtonMatcher };

export const teamsInitialAdmissionIndicators: string[] = [
  // Most reliable indicators: Leave buttons that actually exist in Teams meetings
  'button[id="hangup-button"]',
  'button[data-tid="hangup-main-btn"]',
  'button[aria-label="Leave"]',
  '[role="toolbar"] button[aria-label*="Leave"]',
  'button[aria-label*="Leave"]'
];

export const teamsWaitingRoomIndicators: string[] = [
  // Pre-join screen specific text (generic patterns)
  'text="Someone will let you in shortly"',
  'text=Someone will let you in shortly', // Generic (substring) pattern for any bot name
  'text="You\'re in the lobby"',
  'text="Waiting for someone to let you in"',
  'text="Please wait until someone admits you"',
  'text="Wait for someone to admit you"',
  'text="Waiting to be admitted"',
  'text="Your request to join has been sent"',

  // Pre-join screen specific elements
  'button:has-text("Join now")',
  'button:has-text("Cancel")',
  'text="Microsoft Teams meeting"',

  // Pre-join screen specific aria labels
  '[aria-label*="waiting"]',
  '[aria-label*="lobby"]',
  '[aria-label*="Join now"]',
  '[aria-label*="Cancel"]',

  // Pre-join screen specific classes/attributes
  '[data-tid*="pre-join"]',
  '[data-tid*="lobby"]',
  '[data-tid*="waiting"]',

  // Error states
  'text="Meeting not found"',
  'text="Unable to join"'
];

export const teamsRejectionIndicators: string[] = [
  // Primary rejection message
  'text="Sorry, but you were denied"',
  'text=Sorry, but you were denied',

  // Alternative rejection patterns
  'text="You were denied entry"',
  'text=You were denied entry',
  'text="Access denied"',
  'text=Access denied',
  'text="Entry denied"',
  'text=Entry denied',
  'text="Request denied"',
  'text=Request denied',
  'text="Admission denied"',
  'text=Admission denied',
  'text="Unable to join"',
  'text=Unable to join',
  'text="Connection failed"',
  'text=Connection failed',
  'text="Join failed"',
  'text=Join failed',

  // Rejection dialog elements
  '[role="dialog"]:has-text("denied")',
  '[role="alertdialog"]:has-text("denied")',
  '[role="dialog"]:has-text("failed")',
  '[role="alertdialog"]:has-text("failed")',

  // Rejection button patterns that indicate failure/retry scenarios
  // NOTE: Be very specific here — broad selectors like button:has-text("OK"),
  // button[data-tid*="retry"], or [class*="error"] cause false positives on
  // Teams pre-join/lobby screens. Only include text-based denied indicators.
  'button[aria-label*="denied"]'
];

// Teams removal/error state indicators
export const teamsRemovalIndicators: string[] = [
  // Strong removal/error messages
  'text="You\'ve been removed from this meeting"',
  'text=You\'ve been removed from this meeting',
  'text=You’ve been removed from this meeting', // typographic apostrophe (live Teams copy)
  'text="You have been removed from this meeting"',
  'text=You have been removed from this meeting',
  'text="Removed from meeting"',
  'text=Removed from meeting',

  // Error states
  'text="Meeting ended"',
  'text=Meeting ended',
  'text="Call ended"',
  'text=Call ended',
  'text="Connection lost"',
  'text=Connection lost',
  'text="Unable to connect"',
  'text=Unable to connect'

  // NOTE (#600): no generic role/class catch-alls here. `[role="alert"]`,
  // `[role="alertdialog"]`, `.error-message`, `.connection-error` and
  // `.meeting-error` match ANY Teams alert region — mute toasts, captions,
  // network blips, the post-join AV-confirmation modal — none of which mean the
  // bot was removed. A benign transient alert ~1.5s after admission tripped a
  // false removal → self-leave → `completed(evicted)`, no transcript. The
  // removal signal is the removal/"meeting ended" TEXT above, which is
  // trustworthy; keep this list text-only.
];

// Teams UI interaction selectors
export const teamsContinueButtonSelectors: string[] = [
  'button:has-text("Continue")'
];

// v0.10.5 — Pre-join "Continue without audio or video" confirmation dialog.
//
// Teams renders this modal when the browser denies (or the OS user dismissed)
// camera/mic permission. Wireframe of the modal:
//
//   "Are you sure you don't want audio or video?
//    If you change your mind, select the camera icon by your address bar
//    and then Always allow."
//   [ Continue without audio or video ]    [ X (dismiss) ]
//
// The dialog is intermittent — it fires only when Chromium's media-permission
// state for the host is "denied" at the moment the prejoin page boots. With
// our PulseAudio + headless setup the OS-level perm dialog is auto-handled,
// but Chromium occasionally lands on this confirmation modal anyway (observed
// 2026-04-30 in compose). When it appears, the bot is BLOCKED — the prejoin
// "Join now" button never enables until the modal is dismissed.
//
// We click "Continue without audio or video" — equivalent to dismissing the
// modal. The bot does not need browser-level media permissions to join.
//
// Multi-selector coverage: aria-label, exact text, partial text (locale
// variants), and a generic [role="dialog"] descendant fallback.
export const teamsContinueWithoutMediaSelectors: string[] = [
  // Most specific — exact button text
  'button:has-text("Continue without audio or video")',
  // aria-label variants
  'button[aria-label="Continue without audio or video"]',
  'button[aria-label*="Continue without audio"]',
  // Partial text — handles trailing/leading whitespace
  'button:text-matches("Continue without audio or video", "i")',
  // Inside a dialog (most reliable scope)
  '[role="dialog"] button:has-text("Continue without audio or video")',
  '[role="alertdialog"] button:has-text("Continue without audio or video")',
];

export const teamsJoinButtonSelectors: string[] = [
  'button:has-text("Join")',
  'button:has-text("Join now")'
];

export const teamsCameraButtonSelectors: string[] = [
  'button[aria-label*="Turn off camera"]',
  'button[aria-label*="Turn on camera"]',
  'button[aria-label*="Turn camera off"]',
  'button[aria-label*="Turn camera on"]',
  'button[aria-label*="Turn off video"]',
  'button[aria-label*="Turn on video"]',
  'button[aria-label*="Turn video off"]',
  'button[aria-label*="Turn video on"]'
];

export const teamsVideoOptionsButtonSelectors: string[] = [
  'button[aria-label*="Open video options"]',
  'button[aria-label*="open video options"]',
  'button[aria-label*="Video options"]',
  'button[aria-label*="video options"]',
  'button[aria-label*="Camera options"]',
  'button[aria-label*="camera options"]',
  'button[data-tid*="video-options"]',
  'button:has-text("Open video options")'
];

// Teams audio option selectors (pre-join screen)
export const teamsComputerAudioRadioSelectors: string[] = [
  'radio[aria-label*="Computer audio"]',
  'radio:has-text("Computer audio")',
  '[role="radio"][aria-label*="Computer audio"]'
];

export const teamsDontUseAudioRadioSelectors: string[] = [
  'radio[aria-label*="Don\'t use audio"]',
  'radio:has-text("Don\'t use audio")',
  '[role="radio"][aria-label*="Don\'t use audio"]'
];

// Teams speaker toggle selectors
export const teamsSpeakerEnableSelectors: string[] = [
  'button[aria-label*="Turn speaker on"]',
  'button[aria-label*="Speaker is off"]',
  'button:has-text("Turn speaker on")',
  'button:has-text("Speaker is off")'
];

export const teamsSpeakerDisableSelectors: string[] = [
  'button[aria-label*="Turn speaker off"]',
  'button[aria-label*="Speaker is on"]',
  'button:has-text("Turn speaker off")',
  'button:has-text("Speaker is on")'
];

export const teamsNameInputSelectors: string[] = [
  'input[placeholder*="name"]',
  'input[placeholder*="Name"]',
  'input[type="text"]'
];

// Primary hangup button selector (most reliable)
export const teamsPrimaryHangupButtonSelector = '#hangup-button';

// Teams comprehensive leave matchers (stateless — covers all scenarios).
// BROWSER CONTEXT: consumed inside page.evaluate via document.querySelector —
// declared in browserContextSelectorArrays below, so the validity gate
// CSS-parses every `css` field. Tried in order; first visible match wins.
// Text-labelled buttons (Teams confirmation dialogs name their buttons by
// textContent, not aria-label) are `text` matchers — the `:has-text()`
// semantics, expressed for a plain-CSS engine. Priority order is preserved
// from the working `#hangup-button` first down to generic close/cancel, so a
// confirmation Leave/End cannot be outranked by a stray Close.
export const teamsLeaveButtonMatchers: BrowserContextButtonMatcher[] = [
  // WORKING SELECTORS FIRST - confirmed from logs
  { css: 'button[id="hangup-button"]' }, // ✅ CONFIRMED WORKING - successfully clicked in logs

  // Teams-specific leave/hangup buttons
  { css: 'button[data-tid="hangup-main-btn"]' },

  // Cancel buttons (for awaiting admission/waiting room)
  { css: 'button[aria-label="Cancel"]' },
  { text: 'Cancel' },

  // Leave buttons (for active meetings)
  { css: 'button[aria-label="Leave"]' },
  { text: 'Leave' },

  // More specific leave patterns
  { css: 'button[aria-label*="Leave"]' },
  { css: 'button[aria-label*="leave"]' },
  { css: '[role="toolbar"] button[aria-label*="Leave"]' },

  // End meeting alternatives
  { css: 'button[aria-label*="End meeting"]' },
  { text: 'End meeting' },
  { css: 'button[aria-label*="Hang up"]' },
  { text: 'Hang up' },

  // Close/dismiss alternatives
  { text: 'Close' },
  { css: 'button[aria-label="Close"]' },
  { text: 'Dismiss' },
  { css: 'button[aria-label="Dismiss"]' },

  // Generic cancel patterns
  { css: 'button[aria-label*="Cancel"]' },
  { css: 'button[data-tid*="cancel"]' },
  { css: '[role="button"]', text: 'Cancel' },

  // Confirmation dialog buttons
  { css: '[role="dialog"] button', text: 'Leave' },
  { css: '[role="dialog"] button', text: 'End meeting' },
  { css: '[role="alertdialog"] button', text: 'Leave' },

  // Fallback patterns
  { css: 'input[type="button"][value="Cancel"]' },
  { css: 'input[type="submit"][value="Cancel"]' }
];

// EXECUTION-CONTEXT DECLARATION — consumed by src/shared/selector-validity.test.ts.
// teamsLeaveButtonMatchers ships into page.evaluate and runs through
// document.querySelector, so the gate's LANE 2 additionally CSS-parses every
// `css` field: a Playwright-only `:has-text()` — invalid in that context —
// would otherwise ship green as a dead selector. `text` fields are raw strings
// matched against textContent in-page, never parsed as selectors.
export const browserContextSelectorArrays: string[] = ['teamsLeaveButtonMatchers'];

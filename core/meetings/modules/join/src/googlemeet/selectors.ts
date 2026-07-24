// Centralized Google Meet selectors and indicators
// Keep this file free of runtime logic; export constants only.
//
// TEXT-SELECTOR SEMANTICS (Playwright): quoted `text="foo"` is EXACT match
// (case-sensitive); unquoted `text=foo` is SUBSTRING match (case-insensitive,
// whitespace-normalized). `text*=` is NOT a Playwright engine — such entries
// threw InvalidSelectorError on every locator call and were silently skipped
// by the detectors' try/catch loops (dead selectors). All `text*="foo"`
// entries were replaced with the unquoted substring form on 2026-07-04;
// src/shared/selector-validity.test.ts now gates every selector array.
//
// EXECUTION-CONTEXT SEMANTICS: Playwright engines (`text=`, `:has-text()`)
// exist ONLY on the Playwright side (page.locator). Anything shipped into
// page.evaluate runs through document.querySelector, which understands plain
// CSS and nothing else — a Playwright-only entry there throws SyntaxError on
// every call. Arrays consumed in browser context are declared in
// browserContextSelectorArrays (bottom of this file); the validity gate
// CSS-parses each declared entry, and text-labelled buttons are expressed as
// `{ text: … }` matcher fields, matched in-page against textContent.

export const googleInitialAdmissionIndicators: string[] = [
  // DOM fallback selectors — only indicators that do NOT appear in the lobby.
  // DANGER: Leave call, toolbar, mic/camera toggles all exist in the lobby too!
  // Primary admission signal is active MediaStreams (checked in admission.ts).
  '[data-participant-id]',
  '[data-self-name]',
  'button[aria-label*="Share screen"]',
  'button[aria-label*="Present now"]',
];

export const googleWaitingRoomIndicators: string[] = [
  // Modern waiting room text patterns (2024 Google Meet UI)
  'text="Asking to be let in..."',
  'text=Asking to be let in',
  'text="You\'ll join the call when someone lets you in"',
  'text=You\'ll join the call when someone lets you',
  'text=You’ll join the call when someone lets you', // live Meet copy uses a typographic apostrophe
  'text="Please wait until a meeting host brings you into the call"',
  'text="Waiting for the host to let you in"',
  'text="You\'re in the waiting room"',
  'text="Asking to be let in"',

  // Aria labels and waiting room indicators
  '[aria-label*="waiting room"]',
  '[aria-label*="Asking to be let in"]',
  '[aria-label*="waiting for admission"]',

  // FIX (Vexa-ai/vexa#471, @priitvimberg): the "Asking to be let in" waiting
  // screen shows a "Return to home screen" button. It was listed in
  // googleRejectionIndicators, so the bot false-rejected in ~4s
  // (awaiting_admission_rejected) instead of waiting for the host to admit it.
  // Reclassified as a WAITING indicator; genuine denials are still caught by
  // the "denied your request" text patterns in googleRejectionIndicators.
  'button:has-text("Return to home screen")',
];

// Google's Gemini "take notes for me" in-call consent prompt (Vexa-ai/vexa#454,
// @thatditsyboy; issue #429). This is a consent gate, not mere chrome: until a
// human accepts or declines, the bot is not truly participating, yet the
// surrounding meeting controls can read as "admitted" (status active, 0
// transcriptions). Detected so the bot routes to needs_human_help instead of
// false-reporting ACTIVE.
//
// Targeted at the prompt's distinctive copy ("take notes for me" / "taking
// notes") so it does NOT match the always-present Gemini toolbar button.
//
// NOTE: these selectors are best-effort and SHOULD be confirmed against the
// live prompt DOM (the upstream PR flags its selectors as best-effort) —
// reproduce with a live meeting that has Gemini notes enabled and adjust if
// Google changes the copy.
export const googleConsentPromptIndicators: string[] = [
  // Unquoted text= is substring + case-insensitive, so one entry covers
  // "take notes for me" / "Take notes for me".
  'text=take notes for me',
  '[role="dialog"]:has-text("take notes for me")',
  '[role="alertdialog"]:has-text("take notes for me")',
  'button:has-text("take notes for me")',
  // "taking notes" is dialog-scoped ON PURPOSE: a bare substring would also
  // match the persistent "Gemini is taking notes" in-call pill shown when
  // notes are ALREADY running — that state must not read as a pending consent
  // gate (it would suppress admission for the entire call).
  '[role="dialog"]:has-text("taking notes")',
  '[role="alertdialog"]:has-text("taking notes")',
];

export const googleRejectionIndicators: string[] = [
  // Waiting-room denial patterns. Google Meet can leave some lobby text in
  // the DOM after a host rejects the bot, so these must be checked before
  // waiting-room indicators in admission polling.
  'text=denied your request',
  'text=denied your request to join',
  'text=Your request to join was denied',
  'text=You were denied',
  'text=weren\'t allowed to join',
  'text=weren’t allowed to join', // typographic apostrophe (live Meet copy)
  'text=not allowed to join',
  'text=not admitted',
  'text=can\'t join this call',
  'text=can’t join this call', // typographic apostrophe (live Meet copy)
  'text=cannot join this call',
  'text=Ask to join again',
  'button:has-text("Ask to join again")',

  // Meeting not found or access denied patterns
  'text="Meeting not found"',
  'text="Can\'t join the meeting"',
  'text="Unable to join"',
  'text="Access denied"',
  'text="Meeting has ended"',
  'text="This meeting has ended"',
  'text="Invalid meeting"',
  'text="Meeting link expired"',
  
  // Error dialog indicators (more specific to avoid false positives)
  '[role="dialog"]:has-text("Meeting not found")',
  '[role="alertdialog"]:has-text("Meeting not found")',
  '[role="dialog"]:has-text("meeting not found")',
  '[role="alertdialog"]:has-text("meeting not found")',
  '[role="dialog"]:has-text("Meeting has ended")',
  '[role="alertdialog"]:has-text("Meeting has ended")',
  '[role="dialog"]:has-text("meeting has ended")',
  '[role="alertdialog"]:has-text("meeting has ended")',
  
  // Retry/error buttons
  'button:has-text("Try again")',
  'button:has-text("Retry")',
  'button:has-text("Go back")',
  'button[aria-label*="retry"]',
  'button[aria-label*="try again"]'
];

export const googleAdmissionIndicators: string[] = [
  // Meeting toolbar and controls (most reliable admission indicators)
  'button[aria-label*="Chat"]',
  'button[aria-label*="chat"]',
  'button[aria-label*="People"]',
  'button[aria-label*="people"]',
  'button[aria-label*="Participants"]',
  'button[aria-label*="Leave call"]',
  'button[aria-label*="Leave meeting"]',
  
  // Audio/video controls that appear when in meeting
  'button[aria-label*="Turn off microphone"]',
  'button[aria-label*="Turn on microphone"]',
  'button[aria-label*="Turn off camera"]',
  'button[aria-label*="Turn on camera"]',
  
  // Share and present buttons
  'button[aria-label*="Share screen"]',
  'button[aria-label*="Present now"]',
  
  // Meeting toolbar and controls
  '[role="toolbar"]',
  '[data-participant-id]',
  '[data-self-name]',
  
  // Audio level indicators
  '[data-audio-level]',
  '[aria-label*="microphone"]',
  '[aria-label*="camera"]',
  
  // Meeting controls toolbar
  '[data-tooltip*="microphone"]',
  '[data-tooltip*="camera"]',
  
  // Video tiles and meeting UI
  '[aria-label*="meeting"]',
  'div[data-meeting-id]'
];

// Participant-related selectors for speaker detection
export const googleParticipantSelectors: string[] = [
  'div[data-participant-id]', // Primary Google Meet participant selector
  '[data-participant-id]',
  '[aria-label*="participant"]',
  '[data-self-name]',
  '.participant-tile',
  '.video-tile'
];

export const googleSpeakingClassNames: string[] = [
  'Oaajhc', // Google Meet speaking animation class
  'HX2H7',  // Alternative speaking class
  'wEsLMd', // Another speaking indicator
  'OgVli',  // Additional speaking class
  'speaking', 
  'active-speaker', 
  'speaker-active', 
  'speaking-indicator',
  'audio-active', 
  'mic-active', 
  'microphone-active', 
  'voice-active',
  'speaking-border', 
  'speaking-glow', 
  'speaking-highlight'
];

export const googleSilenceClassNames: string[] = [
  'gjg47c', // Google Meet silence class
  'silent', 
  'muted', 
  'mic-off', 
  'microphone-off', 
  'audio-inactive',
  'participant-silent', 
  'user-silent', 
  'no-audio'
];

export const googleParticipantContainerSelectors: string[] = [
  '[data-participant-id]',
  '[data-self-name]',
  '.participant-tile',
  '.video-tile',
  '[jsname="BOHaEe"]' // Google Meet meeting container
];

// Google Meet name selectors for participant identification
export const googleNameSelectors: string[] = [
  // Google Meet specific name selectors
  'span.notranslate', // Primary name element in Google Meet
  '[data-self-name]',
  '.zWGUib',
  '.cS7aqe.N2K3jd',
  '.XWGOtd',
  '[data-tooltip*="name"]',
  '[aria-label*="name"]',
  '.participant-name',
  '.display-name',
  '.user-name'
];

// Google Meet speaking indicators (primary speaker detection)
export const googleSpeakingIndicators: string[] = [
  // Semantic attribute — survives CSS class rotation across GMeet releases
  '[data-audio-level]:not([data-audio-level="0"])',
  // Obfuscated class names — may rotate with GMeet UI updates
  '.Oaajhc', // Speaking animation class
  '.HX2H7',  // Alternative speaking class
  '.wEsLMd', // Another speaking indicator
  '.OgVli'   // Additional speaking class
];

// Google Meet removal/error state indicators
export const googleRemovalIndicators: string[] = [
  // Meeting ended messages
  'text="Meeting ended"',
  'text=Meeting ended',
  'text="Call ended"',
  'text=Call ended',
  'text="You left the meeting"',
  'text=You left the meeting',

  // Connection issues
  'text="Connection lost"',
  'text=Connection lost',
  'text="Unable to connect"',
  'text=Unable to connect',
  'text="Reconnecting"',
  'text=Reconnecting',
  
  // Generic error patterns
  '[role="alert"]',
  '[role="alertdialog"]',
  '.error-message',
  '.connection-error',
  '.meeting-error'
];

// Google Meet UI interaction selectors.
//
// ORDER IS AUTHORITATIVE: join.ts resolves these lists top-down on every poll,
// so an earlier entry always beats a later one on the same DOM.
//
// The first entry is the locale-agnostic one; the English literals follow. The
// `text()="Ask to join"` / has-text locators only match an English UI, so a
// Hungarian (or any non-English) Meet lobby cannot be joined by them alone
// (prod ids 13951 13952 14018 14153).
//
// COVERAGE LIMIT — read before adding to this list: the structural entry can
// only see a lobby whose CTA carries NO accessible label. `:not([aria-label])`
// is load-bearing (the lobby's aria-labelled 3-dot menu is otherwise a
// `button[jsname]` with a span, and the humanized click lands on the menu), but
// it also blinds this list to a lobby whose CTA IS aria-labelled. That shape is
// NOT closed by widening this list; the real fix is #856 — the browser's UI
// locale is now pinned (`--lang=en-US`, context `locale`), so Meet renders the
// English lobby by construction and the exact-text entries below are correct,
// not lucky. `findLobbyPrimaryCta` in join.ts still scans, but only as a
// diagnostic (it never clicks) — see its docs.
//
// ORDERING IS AUTHORITATIVE: `waitForAnySelector` resolves this list top-down on
// every poll (ordered, not raced), so list position decides which control wins.
// The EXACT text selectors come FIRST and the broad structural
// `button[jsname]:not([aria-label]):has(span)` entry comes LAST — deliberately
// inverting #917's ordering. With the locale pinned, the exact English match is
// the right control; the broad entry can match a wrong jsname+span button that
// happens to sit earlier in DOM order, so it must only win when nothing exact
// does. Do NOT move the structural entry up, and do NOT delete it (it is the
// last-resort locale-agnostic backstop).
export const googleJoinButtonSelectors: string[] = [
  // Exact text FIRST — correct by construction now the UI locale is pinned (#856).
  '//button[.//span[text()="Ask to join"]]',
  'button:has-text("Ask to join")',
  'button:has-text("Join now")',
  'button:has-text("Join")',
  // Broad structural backstop LAST: a real <button> with Google's jsname token, a
  // text span and no accessible label. Only wins if no exact selector matches.
  'button[jsname]:not([aria-label]):has(span)'
];

// Icon-glyph descendants of a lobby <button>. A button containing any of these
// is an icon affordance (mic / camera / 3-dot menu / "cast this meeting" /
// "use a phone for audio"), never the primary admission CTA — Meet renders that
// one as text only. Consumed by `findLobbyPrimaryCta` in join.ts, which runs in
// BROWSER CONTEXT through document.querySelector, so every entry must be plain
// CSS (declared in browserContextSelectorArrays below; the validity gate
// CSS-parses it). Material icons carry their glyph name as a TEXT node
// ("mic_off", "more_vert"), so excluding these elements is what keeps an
// icon-only button from reading as a text-labelled one.
export const googleLobbyIconGlyphSelectors: string[] = [
  'i',
  'svg',
  'img',
  '[class*="material-icons"]',
  '[class*="material-symbols"]',
  '[data-icon-name]'
];

// A primary CTA label is a couple of words in any language ("Ask to join",
// "Kérvényezés a csatlakozásra", "参加をリクエスト"). Anything longer is prose —
// a disclosure/consent paragraph rendered as a button — and is not a CTA.
export const googleLobbyCtaMaxLabelChars = 48;

export const googleCameraButtonSelectors: string[] = [
  '[aria-label*="Turn off camera"]',
  'button[aria-label*="Turn off camera"]',
  'button[aria-label*="Turn on camera"]'
];

export const googleMicrophoneButtonSelectors: string[] = [
  '[aria-label*="Turn off microphone"]',
  'button[aria-label*="Turn off microphone"]',
  'button[aria-label*="Turn on microphone"]'
];

// Name input — locale-agnostic FIRST. `aria-label="Your name"` is English-only,
// so the non-English lobby (Hungarian: "A neved") never matched and the bot
// could not fill its name. The lobby has exactly one editable text input, so
// match by structure/role; keep the English aria-label + placeholder as
// fallbacks.
export const googleNameInputSelectors: string[] = [
  // Locale-agnostic: the single text input in the pre-join lobby form.
  'input[jsname][type="text"]',
  'input[type="text"]:not([aria-hidden="true"])',
  'div[jscontroller] input[type="text"]',
  // English fallbacks.
  'input[type="text"][aria-label="Your name"]',
  'input[placeholder*="name"]',
  'input[placeholder*="Name"]'
];

// Authenticated-lobby primary CTA — "Join now" / "Switch here" / "Ask to join".
// Same ordering rule as googleJoinButtonSelectors above: ordered resolution makes
// list position authoritative, so the EXACT text selectors come FIRST and the
// broad structural `button[jsname]:not([aria-label]):has(span)` entry comes LAST.
// With the UI locale pinned (#856) the English text is correct by construction;
// the broad entry is only the last-resort backstop and must not beat an exact
// match. `findLobbyPrimaryCta` scans here too, diagnostic-only (never clicks).
export const googleAuthJoinCtaSelectors: string[] = [
  // Exact text FIRST.
  'button:has-text("Join now")',
  'button:has-text("Switch here")',
  'button:has-text("Ask to join")',
  // Broad structural backstop LAST.
  'button[jsname]:not([aria-label]):has(span)'
];

// Signed-out guard probe (authenticated mode): a guest lobby renders a name
// input; a signed-in lobby never does, in any locale. Structural selectors
// FIRST so signed-out detection cannot fail open on a non-English lobby; the
// English aria-label is a fallback only. Kept narrower than
// googleNameInputSelectors on purpose — a false positive here refuses a
// legitimate authenticated join, so the broad bare input[type="text"] catch-all
// is excluded.
export const googleSignedOutLobbyProbeSelectors: string[] = [
  'input[jsname][type="text"]',
  'div[jscontroller] input[type="text"]',
  // English fallback.
  'input[type="text"][aria-label="Your name"]'
];

// Google Meet meeting container selectors
export const googleMeetingContainerSelectors: string[] = [
  '[jsname="BOHaEe"]', // Primary Google Meet container
  '[role="main"]',
  'body'
];

// Google Meet participant ID selectors
export const googleParticipantIdSelectors: string[] = [
  '[data-participant-id]',
  '[data-self-name]',
  '[jsinstance]'
];

// Browser-context button matcher — the canonical shape lives in
// ../shared/leave-click (shared with msteams); imported for local use below
// and re-exported so this module's existing importers keep their reference.
import type { BrowserContextButtonMatcher } from "../shared/leave-click";
export type { BrowserContextButtonMatcher };

// Google Meet comprehensive leave matchers (stateless - covers all scenarios).
// BROWSER CONTEXT: consumed inside page.evaluate via document.querySelector —
// declared in browserContextSelectorArrays below, so the validity gate
// CSS-parses every `css` field. Tried in order; first visible match wins.
export const googleLeaveButtonMatchers: BrowserContextButtonMatcher[] = [
  // Primary Google Meet leave button
  { css: 'button[aria-label="Leave call"]' },

  // Alternative leave patterns
  { css: 'button[aria-label*="Leave"]' },
  { css: 'button[aria-label*="leave"]' },
  { css: '[role="toolbar"] button[aria-label*="Leave"]' },

  // End meeting alternatives
  { css: 'button[aria-label*="End meeting"]' },
  { text: 'End meeting' },
  { text: 'End call' },
  { css: 'button[aria-label*="Hang up"]' },
  { text: 'Hang up' },

  // Confirmation dialog buttons (secondary)
  { text: 'Leave meeting' },
  { text: 'Just leave the meeting' },
  { text: 'Leave' },

  // Dialog-specific patterns
  { css: '[role="dialog"] button', text: 'Leave' },
  { css: '[role="dialog"] button', text: 'End meeting' },
  { css: '[role="alertdialog"] button', text: 'Leave' },

  // Generic close/cancel patterns
  { text: 'Close' },
  { css: 'button[aria-label="Close"]' },
  { text: 'Cancel' },
  { css: 'button[aria-label="Cancel"]' },

  // Fallback patterns
  { css: 'input[type="button"][value="Leave"]' },
  { css: 'input[type="submit"][value="Leave"]' }
];

// Google Meet people/participant panel selectors
export const googlePeopleButtonSelectors: string[] = [
  'button[aria-label^="People"]',
  'button[aria-label*="people"]',
  'button[aria-label*="Participants"]',
  'button[aria-label*="participants"]',
  'button[aria-label*="Show people"]',
  'button[aria-label*="show people"]',
  'button[aria-label*="View people"]',
  'button[aria-label*="view people"]',
  'button[aria-label*="Meeting participants"]',
  'button[aria-label*="meeting participants"]',
  'button:has(span:contains("People"))',
  'button:has(span:contains("people"))',
  'button:has(span:contains("Participants"))',
  'button:has(span:contains("participants"))',
  'button[data-mdc-dialog-action]',
  'button[data-tooltip*="people"]',
  'button[data-tooltip*="People"]',
  'button[data-tooltip*="participants"]',
  'button[data-tooltip*="Participants"]'
];

// EXECUTION-CONTEXT DECLARATION — consumed by src/shared/selector-validity.test.ts.
// Arrays named here ship into page.evaluate and run through
// document.querySelector, so the gate additionally CSS-parses them: a
// Playwright parse alone would let `:has-text()` — invalid in that context —
// ship green as a dead selector. Entries may be plain CSS strings or
// BrowserContextButtonMatcher objects (`css` field parsed as CSS; `text`
// fields are raw strings, not selectors).
export const browserContextSelectorArrays: string[] = [
  'googleLeaveButtonMatchers',
  'googleLobbyIconGlyphSelectors',
];


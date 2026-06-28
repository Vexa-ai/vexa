// Zoom Web Client (browser-based) selectors — verified from live DOM inspection
// Navigate to: https://app.zoom.us/wc/MEETING_ID/join?pwd=PASSWORD
//
// NOTE vs the monolith: chat-panel, speaker-tile and footer audio/video selectors
// are RECORDING/observe concerns and stay in vexa-bot. This brick keeps only what
// join / admission / leave / removal read.

// ---- Pre-join page ----

// Name input — two web-client variants:
//   React client  (app.zoom.us/wc/<id>/join):  <input id="input-for-name">
//   Classic client(app.zoom.us/wc/join/<id>):  <input id="inputname" placeholder="Your Name">
// Zoom redirects guest joins to the classic client for some meetings, so match both.
export const zoomNameInputSelector = '#input-for-name, #inputname, input[placeholder="Your Name" i]';

// Join button — React: <button class="zm-btn preview-join-button ..."> (disabled until name);
//               Classic: <button id="joinBtn" class="btn btn-primary ... submit">Join</button>
export const zoomJoinButtonSelector = 'button.preview-join-button, #joinBtn';

// Mute button in preview: <button id="preview-audio-control-button" aria-label="Mute">
export const zoomPreviewMuteSelector = '#preview-audio-control-button';

// Stop Video button in preview: <button id="preview-video-control-button" aria-label="Stop Video">
export const zoomPreviewVideoSelector = '#preview-video-control-button';

// Permission dialog (React portal): shown twice — once for camera+mic, once for mic only
// Button text: "Continue without microphone and camera"
export const zoomPermissionDismissSelector = 'button:has-text("Continue without microphone and camera")';

// ---- In-meeting admission indicators ----

// Leave button: most reliable signal that bot is inside the meeting
// <button aria-label="Leave" class="footer-button-base__button ax-outline footer-button__button">
export const zoomLeaveButtonSelector = 'button[aria-label="Leave"]';

// The meeting app container
export const zoomMeetingAppSelector = '.meeting-app';

// ---- Host-not-started / invalid meeting ----
// When host hasn't started: title="Error - Zoom", text="This meeting link is invalid (3,001)"
export const zoomInvalidMeetingText = 'This meeting link is invalid';
export const zoomInvalidMeetingTitle = 'Error - Zoom';

// ---- Waiting room indicators ----
// Zoom waiting room: specific text strings appear in DOM (no unique CSS class)
export const zoomWaitingRoomTexts = [
  'Please wait, the meeting host will let you in soon.',
  'Please wait',
  'Waiting for the host to start this meeting',
  'Waiting for the host to start the meeting',
  'waiting room',
  'Waiting Room',
  'Host has joined. We\'ve let them know you\'re here',
];

// ---- Removal / end-of-meeting indicators ----
// Modal: <div class="zm-modal-body-title">This meeting has been ended by host</div>
export const zoomMeetingEndedModalSelector = '.zm-modal-body-title';
export const zoomRemovalTexts = [
  'This meeting has been ended by host',
  'removed from the meeting',
  'meeting has ended',
  'Meeting has ended',
  'ended by the host',
  'You have been removed',
  'host ended the meeting',
];

// ---- Post-Join anti-bot wall (RTMS-required) ----
// Some meetings/accounts serve a hard anti-bot wall AFTER the bot clicks Join
// (the admission phase), instead of the waiting room or the meeting:
//   "We detected you may be a bot. Automated bots aren't allowed to join this
//    meeting or webinar and must use Zoom RTMS. … Sign in to join" + reCAPTCHA.
// Verified identical from datacenter AND residential IPs on the same meeting →
// it is the meeting/account anti-bot setting, NOT IP reputation. The path the
// wall points to (Zoom RTMS) is a server-side API, not a browser join, so the
// honest outcome is to detect this, fail fast (reason: zoom_requires_rtms), and
// route to RTMS — NOT to attempt evasion. Case-insensitive substring match.
export const zoomBotBlockTexts = [
  "automated bots aren't allowed",
  "automated bots aren’t allowed", // curly apostrophe variant Zoom renders
  "must use Zoom RTMS",
  "detected you may be a bot",
  "sign in to join",
];

// ---- Leave dialog (after clicking Leave button) ----
// Verified from live DOM: the "Leave Meeting" button has class leave-meeting-options__btn--danger
// aria-label is empty so text-based selectors are unreliable; use the CSS class directly
export const zoomLeaveConfirmSelector = 'button.leave-meeting-options__btn--danger';

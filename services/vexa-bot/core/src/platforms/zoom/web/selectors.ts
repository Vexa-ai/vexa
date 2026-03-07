// Zoom Web Client (browser-based) selectors — verified from live DOM inspection
// Navigate to: https://app.zoom.us/wc/MEETING_ID/join?pwd=PASSWORD

// ---- Pre-join page ----

// Name input: <input id="input-for-name">
export const zoomNameInputSelector = '#input-for-name';

// Join button: <button class="zm-btn preview-join-button ..."> — disabled until name entered
export const zoomJoinButtonSelector = 'button.preview-join-button';

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

// Audio button in footer (shown when in meeting)
// class="footer-button-base__button ax-outline join-audio-container__btn"
export const zoomAudioButtonSelector = 'button.join-audio-container__btn';

// Video button in footer
export const zoomVideoButtonSelector = 'button.send-video-container__btn';

// Participants button: aria-label contains "participants list"
export const zoomParticipantsButtonSelector = 'button[aria-label*="participants list"]';

// Chat button
export const zoomChatButtonSelector = 'button[aria-label*="chat panel"]';

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
  'waiting room',
  'Waiting Room',
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

// ---- Chat panel DOM (when open) ----
// Chat input: TipTap ProseMirror contenteditable inside the RTF editor wrapper
// Verified from live DOM: .chat-rtf-box__editor-outer > .chat-rtf-box__editor-wrapper > ._rtfEditor_* > div[contenteditable]
export const zoomChatInputSelector = '.chat-rtf-box__editor-outer [contenteditable="true"], .tiptap.ProseMirror';
// Chat send button: aria-label="send" on the footer send button
export const zoomChatSendButtonSelector = 'button[aria-label="send"], button[class*="chat-rtf-box__send"]';
// Chat message container — verified: .new-chat-message__container wraps each message
export const zoomChatMessageSelector = '.new-chat-message__container';
// Sender name — verified: .chat-item__sender inside message list items
export const zoomChatSenderSelector = '.chat-item__sender';
// Message text content — verified: .new-chat-message__text-box or .chat-rtf-box__display
export const zoomChatTextSelector = '.new-chat-message__text-box, .chat-rtf-box__display';
// Chat notification banner (new messages)
export const zoomChatNotificationSelector = '[class*="notification-message"]';

// ---- Speaker / participant DOM (in-meeting) ----
// Active speaker tile (main large video frame)
export const zoomActiveSpeakerSelector = '.speaker-active-container__video-frame';
// Speaker bar (non-active thumbnails)
export const zoomSpeakerBarSelector = '.speaker-bar-container__video-frame';
// Participant name label — verified from live DOM: name is in .video-avatar__avatar-footer > span
// (NOT .video-avatar__avatar-name — that element doesn't exist in Zoom Web Client)
export const zoomParticipantNameSelector = '.video-avatar__avatar-footer';
// All video avatar containers
export const zoomVideoAvatarSelector = '.video-avatar__avatar';

// ---- Leave dialog (after clicking Leave button) ----
export const zoomLeaveConfirmSelector = 'button:has-text("Leave Meeting")';
export const zoomEndForAllSelector = 'button:has-text("End for All")';

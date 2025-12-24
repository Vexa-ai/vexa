// Centralized MS Teams selectors and indicators
// Keep this file free of runtime logic; export constants only.

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
  'text*="Someone will let you in shortly"', // Generic pattern for any bot name
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
  'text*="Sorry, but you were denied"',
  
  // Alternative rejection patterns
  'text="You were denied entry"',
  'text*="You were denied entry"',
  'text="Access denied"',
  'text*="Access denied"',
  'text="Entry denied"',
  'text*="Entry denied"',
  'text="Request denied"',
  'text*="Request denied"',
  'text="Admission denied"',
  'text*="Admission denied"',
  'text="Unable to join"',
  'text*="Unable to join"',
  'text="Connection failed"',
  'text*="Connection failed"',
  'text="Join failed"',
  'text*="Join failed"',
  
  // Rejection dialog elements
  '[role="dialog"]:has-text("denied")',
  '[role="alertdialog"]:has-text("denied")',
  '[role="dialog"]:has-text("failed")',
  '[role="alertdialog"]:has-text("failed")',
  
  // Rejection button patterns that indicate failure/retry scenarios
  'button:has-text("Try again")',
  'button:has-text("Retry")',
  'button:has-text("OK")',
  'button[aria-label*="denied"]',
  'button[data-tid*="retry"]',
  'button[data-tid="calling-retry-cancelbutton"]', // Specific pattern from logs
  
  // Error state indicators
  '[data-tid*="error"]',
  '[data-tid*="failed"]',
  '[class*="error"]',
  '[class*="failed"]'
];

export const teamsAdmissionIndicators: string[] = [
  // Most reliable indicators - meeting-specific elements that don't exist in pre-join
  'div:has-text("In this meeting")',
  'div[aria-label*="In this meeting"]',
  'div:has-text("Waiting in lobby")',
  'div[aria-label*="Waiting in lobby"]',
  
  // Meeting toolbar with specific controls (not pre-join toolbar)
  '[role="toolbar"] button[aria-label*="Share"]',
  '[role="toolbar"] button[aria-label*="Present"]',
  '[role="toolbar"] button[aria-label*="Leave"]',
  '[role="toolbar"] button[aria-label*="End meeting"]',
  
  // Meeting navigation tabs (active in meeting, not pre-join)
  'button[aria-label*="Chat"]:not([disabled])',
  'button[aria-label*="People"]:not([disabled])',
  'button[aria-label*="Participants"]:not([disabled])',
  
  // Meeting-specific audio/video controls (enabled, not pre-join disabled state)
  'button[aria-label*="Turn off microphone"]:not([disabled])',
  'button[aria-label*="Turn on microphone"]:not([disabled])',
  'button[aria-label*="Turn off camera"]:not([disabled])',
  'button[aria-label*="Turn on camera"]:not([disabled])',
  
  // Meeting-specific UI elements
  '[data-tid*="meeting-controls"]',
  '[data-tid*="call-controls"]',
  '[data-tid*="meeting-toolbar"]',
  '[data-tid*="participants-panel"]',
  
  // Meeting-specific data attributes
  '[data-tid*="meeting"]',
  '[data-tid*="call"]'
];

// Participant-related selectors and class names for speaker detection
export const teamsParticipantSelectors: string[] = [
  '[data-tid*="participant"]',
  '[aria-label*="participant"]',
  '[data-tid*="roster"]',
  '[data-tid*="roster-item"]',
  '[data-tid*="video-tile"]',
  '[data-tid*="videoTile"]',
  '[data-tid*="participant-tile"]',
  '[data-tid*="participantTile"]',
  '[role="listitem"]',
  '.participant-tile',
  '.video-tile',
  '.roster-item'
];

export const teamsSpeakingClassNames: string[] = [
  'speaking', 'active-speaker', 'speaker-active', 'speaking-indicator',
  'audio-active', 'mic-active', 'microphone-active', 'voice-active',
  'speaking-border', 'speaking-glow', 'speaking-highlight',
  'participant-speaking', 'user-speaking', 'speaker-indicator'
];

export const teamsSilenceClassNames: string[] = [
  'silent', 'muted', 'mic-off', 'microphone-off', 'audio-inactive',
  'participant-silent', 'user-silent', 'no-audio'
];

export const teamsParticipantContainerSelectors: string[] = [
  '[data-tid*="participant"]',
  '[data-tid*="roster-item"]',
  '[data-tid*="video-tile"]',
  '[data-tid*="videoTile"]',
  '.participant-tile',
  '.video-tile'
];

// Leave button selectors (used in browser context via page.evaluate)
export const teamsPrimaryLeaveButtonSelectors: string[] = [
  // Specific Teams leave button attributes (most reliable)
  'button[data-tid="hangup-main-btn"]',
  'button[id="hangup-button"]',
  'button[aria-label="Leave"]',
  
  // Generic aria-label patterns
  'button[aria-label*="Leave"]',
  'button[aria-label*="leave"]',
  'button[aria-label*="End meeting"]',
  'button[aria-label*="end meeting"]',
  'button[aria-label*="Hang up"]',
  'button[aria-label*="hang up"]',
  
  // Role-based selectors
  '[role="toolbar"] button[aria-label*="Leave"]',
  '[role="toolbar"] button[data-tid="hangup-main-btn"]'
];

export const teamsSecondaryLeaveButtonSelectors: string[] = [
  // Text-based selectors (for confirmation dialogs)
  'button:has-text("Leave meeting")',
  'button:has-text("Leave")',
  'button:has-text("End meeting")',
  'button:has-text("Hang up")',
  'button:has-text("End call")',
  'button:has-text("Leave call")',
  
  // Confirmation dialog specific selectors
  '[role="dialog"] button:has-text("Leave")',
  '[role="dialog"] button:has-text("End meeting")',
  '[role="alertdialog"] button:has-text("Leave")',
  
  // Generic confirmation patterns
  'button[aria-label*="confirm"]:has-text("Leave")',
  'button[aria-label*="confirm"]:has-text("End")'
];

// Teams name selectors for participant identification
export const teamsNameSelectors: string[] = [
  // Look for the actual name div structure
  'div[class*="___2u340f0"]', // The actual name div class pattern
  '[data-tid*="display-name"]',
  '[data-tid*="participant-name"]',
  '[data-tid*="user-name"]',
  '[aria-label*="name"]',
  '.participant-name',
  '.display-name',
  '.user-name',
  '.roster-item-name',
  '.video-tile-name',
  'span[title]',
  '[title*="name"]',
  '.ms-Persona-primaryText',
  '.ms-Persona-secondaryText'
];

// Teams speaking indicators (primary voice level detection)
export const teamsSpeakingIndicators: string[] = [
  '[data-tid="voice-level-stream-outline"]'
];

// Teams removal/error state indicators
export const teamsRemovalIndicators: string[] = [
  // Strong removal/error messages
  'text="You\'ve been removed from this meeting"',
  'text*="You\'ve been removed from this meeting"',
  'text="You have been removed from this meeting"',
  'text*="You have been removed from this meeting"',
  'text="Removed from meeting"',
  'text*="Removed from meeting"',
  
  // Error states
  'text="Meeting ended"',
  'text*="Meeting ended"',
  'text="Call ended"',
  'text*="Call ended"',
  'text="Connection lost"',
  'text*="Connection lost"',
  'text="Unable to connect"',
  'text*="Unable to connect"',
  
  // Generic error patterns
  '[role="alert"]',
  '[role="alertdialog"]',
  '.error-message',
  '.connection-error',
  '.meeting-error'
];

// Teams UI interaction selectors
export const teamsContinueButtonSelectors: string[] = [
  'button:has-text("Continue")'
];

export const teamsJoinButtonSelectors: string[] = [
  'button:has-text("Join")',
  'button:has-text("Join now")'
];

export const teamsCameraButtonSelectors: string[] = [
  'button[aria-label*="Turn off camera"]',
  'button[aria-label*="Turn on camera"]'
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

// Teams meeting container selectors
export const teamsMeetingContainerSelectors: string[] = [
  '[role="main"]',
  'body'
];

// Teams voice level detection selectors
export const teamsVoiceLevelSelectors: string[] = [
  '[data-tid="voice-level-stream-outline"]'
];

// Teams occlusion detection selectors
export const teamsOcclusionSelectors: string[] = [
  '.vdi-frame-occlusion'
];

// Teams stream type selectors
export const teamsStreamTypeSelectors: string[] = [
  '[data-stream-type]'
];

// Teams audio activity selectors
export const teamsAudioActivitySelectors: string[] = [
  '[class*="voice" i][class*="level" i]',
  '[class*="speaking" i]',
  '[data-audio-active="true"]'
];

// Teams participant ID selectors
export const teamsParticipantIdSelectors: string[] = [
  '[data-tid]',
  '[data-participant-id]',
  '[data-user-id]'
];

// Primary hangup button selector (most reliable)
export const teamsPrimaryHangupButtonSelector = '#hangup-button';

// Teams comprehensive leave selectors (stateless - covers all scenarios)
export const teamsLeaveSelectors: string[] = [
  // WORKING SELECTORS FIRST - confirmed from logs
  'button[id="hangup-button"]', // ✅ CONFIRMED WORKING - successfully clicked in logs
  
  // Teams-specific leave/hangup buttons
  'button[data-tid="hangup-main-btn"]',
  
  // Cancel buttons (for awaiting admission/waiting room)
  'button[aria-label="Cancel"]',
  'button:has-text("Cancel")',
  
  // Leave buttons (for active meetings)
  'button[aria-label="Leave"]',
  'button:has-text("Leave")',
  
  // More specific leave patterns
  'button[aria-label*="Leave"]',
  'button[aria-label*="leave"]',
  '[role="toolbar"] button[aria-label*="Leave"]',
  
  // End meeting alternatives
  'button[aria-label*="End meeting"]',
  'button:has-text("End meeting")',
  'button[aria-label*="Hang up"]',
  'button:has-text("Hang up")',
  
  // Close/dismiss alternatives
  'button:has-text("Close")',
  'button[aria-label="Close"]',
  'button:has-text("Dismiss")',
  'button[aria-label="Dismiss"]',
  
  // Generic cancel patterns
  'button[aria-label*="Cancel"]',
  'button[data-tid*="cancel"]',
  '[role="button"]:has-text("Cancel")',
  
  // Confirmation dialog buttons
  '[role="dialog"] button:has-text("Leave")',
  '[role="dialog"] button:has-text("End meeting")',
  '[role="alertdialog"] button:has-text("Leave")',
  
  // Fallback patterns
  'input[type="button"][value="Cancel"]',
  'input[type="submit"][value="Cancel"]'
];



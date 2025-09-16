// Centralized MS Teams selectors and indicators
// Keep this file free of runtime logic; export constants only.

export const teamsInitialAdmissionIndicators: string[] = [
  // Single robust indicator: visible Leave button in meeting toolbar
  '[role="toolbar"] [aria-label*="Leave"]:visible:not([aria-disabled="true"])'
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
  '[data-tid="voice-level-stream-outline"]', // Main speaker indicator
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
  'button[aria-label*="Leave"]',
  'button[aria-label*="leave"]',
  'button[aria-label*="End meeting"]',
  'button[aria-label*="end meeting"]',
  'button[aria-label*="Hang up"]',
  'button[aria-label*="hang up"]'
];

export const teamsSecondaryLeaveButtonSelectors: string[] = [
  'button:has-text("Leave meeting")',
  'button:has-text("Leave")',
  'button:has-text("End meeting")',
  'button:has-text("Hang up")'
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



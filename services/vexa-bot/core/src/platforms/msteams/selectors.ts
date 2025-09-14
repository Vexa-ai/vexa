// Centralized MS Teams selectors and indicators
// Keep this file free of runtime logic; export constants only.

export const teamsInitialAdmissionIndicators: string[] = [
  'button[aria-label*="People"]',
  'button[aria-label*="people"]',
  'button[aria-label*="Chat"]',
  'button[aria-label*="chat"]',
  'button[aria-label*="Leave"]',
  'button[aria-label*="leave"]',
  'button[aria-label*="End meeting"]',
  'button[aria-label*="end meeting"]',
  '[role="toolbar"]',
  'button[aria-label*="Turn off microphone"]',
  'button[aria-label*="Turn on microphone"]',
  'button[aria-label*="Mute"]',
  'button[aria-label*="mute"]',
  'button[aria-label*="Camera"]',
  'button[aria-label*="camera"]'
];

export const teamsWaitingRoomIndicators: string[] = [
  'text="You\'re in the lobby"',
  'text="Waiting for someone to let you in"',
  'text="Please wait until someone admits you"',
  'text="Wait for someone to admit you"',
  'text="Waiting to be admitted"',
  '[aria-label*="waiting"]',
  '[aria-label*="lobby"]',
  'text="Your request to join has been sent"',
  'text="Meeting not found"'
];

export const teamsAdmissionIndicators: string[] = [
  // Most common Teams meeting indicators (check these first!)
  'button[aria-label*="Chat"]',
  'button[aria-label*="chat"]',
  'button[aria-label*="People"]',
  'button[aria-label*="people"]',
  'button[aria-label*="Participants"]',
  'button[aria-label*="Leave"]',
  'button[aria-label*="leave"]',
  // Audio/video controls that appear when in Teams meeting
  'button[aria-label*="Turn off microphone"]',
  'button[aria-label*="Turn on microphone"]',
  'button[aria-label*="Mute"]',
  'button[aria-label*="mute"]',
  'button[aria-label*="Turn off camera"]',
  'button[aria-label*="Turn on camera"]',
  'button[aria-label*="Camera"]',
  'button[aria-label*="camera"]',
  // Share and present buttons
  'button[aria-label*="Share"]',
  'button[aria-label*="share"]',
  'button[aria-label*="Present"]',
  'button[aria-label*="present"]',
  // Meeting toolbar and controls
  '[role="toolbar"]',
  // Teams specific meeting UI
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



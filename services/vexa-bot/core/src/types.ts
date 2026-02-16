export type BotConfig = {
  platform: "google_meet" | "zoom" | "teams",
  meetingUrl: string | null,
  botName: string,
  token: string,  // MeetingToken (HS256 JWT)
  obfToken?: string,
  connectionId: string,
  nativeMeetingId: string,
  language?: string | null,
  task?: string | null,
  transcribeEnabled?: boolean,
  transcriptionTier?: "realtime" | "deferred",
  redisUrl: string,
  container_name?: string,
  automaticLeave: {
    waitingRoomTimeout: number,
    noOneJoinedTimeout: number,
    everyoneLeftTimeout: number
  },
  reconnectionIntervalMs?: number,
  meeting_id: number,  // Required, not optional
  botManagerCallbackUrl?: string;
  recordingEnabled?: boolean;
  captureModes?: string[];  // e.g., ['audio'], ['audio', 'video'], ['audio', 'screenshots']
  recordingUploadUrl?: string;  // bot-manager internal upload endpoint

  // Voice agent / meeting interaction interface
  voiceAgentEnabled?: boolean;  // Enable TTS, chat, screen share capabilities
  defaultAvatarUrl?: string;   // Custom default avatar image URL for virtual camera
}

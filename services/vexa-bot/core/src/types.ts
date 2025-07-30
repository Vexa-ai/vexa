export type BotConfig = {
  platform: "google_meet" | "zoom" | "teams" | "google_okta",
  meetingUrl: string | null,
  botName: string,
  token: string,
  connectionId: string,
  nativeMeetingId: string,
  language?: string | null,
  task?: string | null,
  redisUrl: string,
  automaticLeave: {
    waitingRoomTimeout: number,
    noOneJoinedTimeout: number,
    everyoneLeftTimeout: number
  },
  reconnectionIntervalMs?: number,
  meeting_id?: number,
  botManagerCallbackUrl?: string,
  credentials?: {
    googleUsername?: string,
    googlePassword?: string,
    mfaSecret?: string
  }
}

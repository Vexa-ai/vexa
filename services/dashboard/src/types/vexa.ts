// Vexa API Types

export type Platform = "google_meet" | "teams" | "zoom" | "jitsi" | "browser_session";

export type MeetingStatus =
  | "idle"
  | "scheduled"
  | "requested"
  | "joining"
  | "awaiting_admission"
  | "active"
  | "needs_human_help"
  | "stopping"
  | "completed"
  | "failed";

export interface Meeting {
  id: string;
  platform: Platform;
  platform_specific_id: string;
  status: MeetingStatus;
  start_time: string | null;
  end_time: string | null;
  bot_container_id: string | null;
  data: MeetingData;
  created_at: string;
  updated_at?: string;
}

// Status transition record from Vexa API
export interface StatusTransition {
  from: MeetingStatus | string;
  to: MeetingStatus | string;
  timestamp: string;
  source?: string;
  reason?: string;
  completion_reason?: string;
  container_id?: string;
  finalized_by?: string;
}

export type OutcomeClass = "ours" | "host_or_platform" | "user" | "normal" | "unknown" | "pending";

export interface MeetingData {
  name?: string;
  title?: string;
  notes?: string;
  participants?: string[];
  languages?: string[];
  // Bot status details (may be populated by Vexa API)
  error?: string;
  error_code?: string;
  status_message?: string;
  failure_reason?: string;
  // Completion details
  completion_reason?: string;
  // How the run ended, graded by meeting-api at the terminal transition.
  // Only "ours" is a failure of the service; see getOutcomeInfo.
  outcome_class?: OutcomeClass;
  outcome_reason?: string;
  // Status history
  status_transition?: StatusTransition[];
  calendar_uid?: string;
  calendar_connection_id?: string;
  calendar_name?: string;
  calendar_sources?: Array<{
    id: string;
    name: string;
    uid: string;
    auto_join: boolean;
    bot_name?: string;
    event?: CalendarEventSnapshot;
  }>;
  attendees?: Array<{
    email?: string;
    name?: string;
    partstat?: string;
  }>;
  [key: string]: unknown;
}

export interface CalendarPropertyValue {
  value: string;
  parameters?: Record<string, string | string[]>;
}

export interface CalendarComponentSnapshot {
  name: string;
  properties: Record<string, CalendarPropertyValue[]>;
  components?: CalendarComponentSnapshot[];
}

export interface CalendarEventSnapshot {
  resolved_start: string;
  calendar: CalendarComponentSnapshot;
  component: CalendarComponentSnapshot;
  series_master?: CalendarComponentSnapshot;
}

export interface TranscriptSegment {
  id: string;
  meeting_id: string;
  start_time: number;
  end_time: number;
  absolute_start_time: string;
  absolute_end_time: string;
  text: string;
  speaker: string;
  language: string;
  completed?: boolean;
  session_uid: string;
  created_at: string;
  updated_at?: string;
  /** Stable segment identity from bot: {session_uid}:{speakerId}:{seq} */
  segment_id?: string;
}

export interface CreateBotRequest {
  platform: Platform;
  native_meeting_id: string;
  passcode?: string;
  meeting_url?: string;
  bot_name?: string;
  language?: string;
  transcribe_enabled?: boolean;
  authenticated?: boolean;
}

export interface BotConfigUpdate {
  language?: string;
  task?: "transcribe" | "translate";
  bot_name?: string;
}

// WebSocket Types
export type WebSocketMessageType =
  | "transcript"
  | "transcript.mutable"
  | "transcript.finalized"
  | "meeting.status"
  | "subscribed"
  | "pong"
  | "error";

export interface WebSocketSubscribeMessage {
  action: "subscribe";
  meetings: Array<{
    platform: Platform;
    native_id: string;
  }>;
}

export interface WebSocketPingMessage {
  action: "ping";
}

// Raw segment from WebSocket (different from stored TranscriptSegment)
export interface WebSocketSegment {
  text: string;
  speaker: string | null;
  language?: string;
  session_uid?: string;
  segment_id?: string;
  completed?: boolean;
  start?: number;
  end_time?: number;
  absolute_start_time: string;
  absolute_end_time: string;
  updated_at?: string;
}

export interface WebSocketTranscriptMessage {
  type: "transcript.mutable" | "transcript.finalized";
  meeting: { id: number };
  payload: {
    segments: WebSocketSegment[];
  };
  ts: string;
}

export interface WebSocketTranscriptBundleMessage {
  type: "transcript";
  meeting: { id: number };
  speaker: string;
  confirmed: WebSocketSegment[];
  pending: WebSocketSegment[];
  ts: string;
}

export interface WebSocketStatusMessage {
  type: "meeting.status";
  meeting: { platform: Platform; native_id: string };
  payload: {
    status: MeetingStatus;
  };
  ts: string;
}

export interface WebSocketSubscribedMessage {
  type: "subscribed";
  meetings: number[];  // Array of meeting IDs
}

export interface WebSocketPongMessage {
  type: "pong";
}

export interface WebSocketErrorMessage {
  type: "error";
  error: string;
  details?: string;
}

// Chat message from the meeting chat (read by the bot)
export interface ChatMessage {
  sender: string;
  text: string;
  timestamp: number;    // Unix ms
  is_from_bot: boolean;
}

export interface WebSocketChatMessage {
  type: "chat.new_message";
  meeting: { id: number };
  payload: ChatMessage;
  ts: string;
}

export type WebSocketIncomingMessage =
  | WebSocketTranscriptMessage
  | WebSocketTranscriptBundleMessage
  | WebSocketStatusMessage
  | WebSocketChatMessage
  | WebSocketSubscribedMessage
  | WebSocketPongMessage
  | WebSocketErrorMessage;

// API Response Types
export interface MeetingsResponse {
  meetings: Meeting[];
}

export interface TranscriptsResponse {
  segments: TranscriptSegment[];
}

// UI Types
export interface SpeakerColor {
  bg: string;
  text: string;
  border: string;
  avatar: string;
}

export const SPEAKER_COLORS: SpeakerColor[] = [
  { bg: "bg-blue-50", text: "text-blue-700", border: "border-blue-200", avatar: "bg-blue-500" },
  { bg: "bg-emerald-50", text: "text-emerald-700", border: "border-emerald-200", avatar: "bg-emerald-500" },
  { bg: "bg-purple-50", text: "text-purple-700", border: "border-purple-200", avatar: "bg-purple-500" },
  { bg: "bg-amber-50", text: "text-amber-700", border: "border-amber-200", avatar: "bg-amber-500" },
  { bg: "bg-rose-50", text: "text-rose-700", border: "border-rose-200", avatar: "bg-rose-500" },
  { bg: "bg-cyan-50", text: "text-cyan-700", border: "border-cyan-200", avatar: "bg-cyan-500" },
  { bg: "bg-indigo-50", text: "text-indigo-700", border: "border-indigo-200", avatar: "bg-indigo-500" },
  { bg: "bg-teal-50", text: "text-teal-700", border: "border-teal-200", avatar: "bg-teal-500" },
];

export function getSpeakerColor(speaker: string, speakerList: string[]): SpeakerColor {
  const index = speakerList.indexOf(speaker);
  if (index === -1) {
    return SPEAKER_COLORS[0];
  }
  return SPEAKER_COLORS[index % SPEAKER_COLORS.length];
}

// Platform display helpers
export const PLATFORM_CONFIG = {
  google_meet: {
    name: "Google Meet",
    color: "bg-green-500",
    textColor: "text-green-700",
    bgColor: "bg-green-50",
    icon: "video",
    pattern: /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/,
    placeholder: "abc-defg-hij",
  },
  teams: {
    name: "Microsoft Teams",
    color: "bg-blue-600",
    textColor: "text-blue-700",
    bgColor: "bg-blue-50",
    icon: "users",
    pattern: /^\d+$/,
    placeholder: "123456789",
  },
  zoom: {
    name: "Zoom",
    color: "bg-blue-500",
    textColor: "text-blue-600",
    bgColor: "bg-blue-50",
    icon: "video",
    pattern: /^\d{9,11}$/,
    placeholder: "85173157171",
  },
  jitsi: {
    name: "Jitsi Meet",
    color: "bg-blue-500",
    textColor: "text-blue-600",
    bgColor: "bg-blue-50",
    icon: "video",
    pattern: /^.+$/,
    placeholder: "meeting-room",
  },
  browser_session: {
    name: "Browser",
    color: "bg-gray-500",
    textColor: "text-gray-700",
    bgColor: "bg-gray-50",
    icon: "monitor",
    pattern: /^bs-/,
    placeholder: "",
  },
} as const;

export const MEETING_STATUS_CONFIG: Record<MeetingStatus, { label: string; color: string; bgColor: string }> = {
  idle: { label: "Planned", color: "text-slate-600 dark:text-slate-400", bgColor: "bg-slate-100 dark:bg-slate-900/50" },
  scheduled: { label: "Scheduled", color: "text-blue-600 dark:text-blue-400", bgColor: "bg-blue-100 dark:bg-blue-950/50" },
  requested: { label: "Requested", color: "text-blue-600 dark:text-blue-400", bgColor: "bg-blue-100 dark:bg-blue-950/50" },
  joining: { label: "Joining", color: "text-blue-600 dark:text-blue-400", bgColor: "bg-blue-100 dark:bg-blue-950/50" },
  awaiting_admission: { label: "Waiting", color: "text-amber-600 dark:text-amber-400", bgColor: "bg-amber-100 dark:bg-amber-950/50" },
  active: { label: "Active", color: "text-green-600 dark:text-green-400", bgColor: "bg-green-100 dark:bg-green-950/50" },
  needs_human_help: { label: "Needs Help", color: "text-orange-600 dark:text-orange-400", bgColor: "bg-orange-100 dark:bg-orange-950/50" },
  stopping: { label: "Stopping", color: "text-slate-600 dark:text-slate-400", bgColor: "bg-slate-100 dark:bg-slate-900/50" },
  completed: { label: "Completed", color: "text-green-600 dark:text-green-400", bgColor: "bg-green-100 dark:bg-green-950/50" },
  failed: { label: "Failed", color: "text-red-600 dark:text-red-400", bgColor: "bg-red-100 dark:bg-red-950/50" },
};

// Get detailed status info based on meeting data
export interface DetailedStatusInfo {
  label: string;
  color: string;
  bgColor: string;
  description?: string;
}

const OUTCOME_TONE: Record<"ok" | "neutral" | "host" | "error", { color: string; bgColor: string }> = {
  ok: { color: "text-green-600 dark:text-green-400", bgColor: "bg-green-100 dark:bg-green-950/50" },
  neutral: { color: "text-gray-600 dark:text-gray-400", bgColor: "bg-gray-100 dark:bg-gray-800/50" },
  host: { color: "text-amber-600 dark:text-amber-400", bgColor: "bg-amber-100 dark:bg-amber-950/50" },
  error: { color: "text-red-600 dark:text-red-400", bgColor: "bg-red-100 dark:bg-red-950/50" },
};

function hostOutcome(reason: string): [string, string] {
  if (reason === "rejected_by_host") return ["Host rejected the bot", "The host declined the bot's request to join"];
  if (reason === "evicted_by_host") return ["Removed by host", "The host removed the bot from the meeting"];
  if (reason === "tenant_requires_sign_in") return ["Your organisation requires sign-in", "This meeting only admits signed-in members of the organisation"];
  if (reason === "host_requires_signed_in_users") return ["Host requires sign-in", "The host only admits signed-in users"];
  if (reason === "platform_blocks_bots" || reason === "platform_captcha") return ["Blocked by the platform", "The meeting platform did not let the bot join"];
  if (reason === "host_never_started") return ["Host never started the meeting", "The meeting was never started by its host"];
  if (reason === "meeting_ended_before_admission") return ["Meeting ended", "The meeting ended before the bot was admitted"];
  if (reason.startsWith("unsupported_url")) return ["Link not supported", "This kind of meeting link is not supported"];
  return ["Not admitted by host", "Nobody admitted the bot from the waiting room"];
}

function userOutcome(reason: string): [string, string] {
  if (reason === "malformed_request" || reason === "malformed_url" || reason === "not_a_meeting_url") return ["Invalid meeting link", "The request did not contain a valid meeting link"];
  if (reason === "dead_or_expired_link") return ["Link expired", "The meeting link is no longer valid"];
  if (reason === "missing_passcode") return ["Passcode required", "The meeting needs a passcode that was not provided"];
  if (reason === "deleted_by_user") return ["Deleted", "The meeting's artifacts were deleted"];
  if (reason === "needs_help_unanswered") return ["Needed your help", "The bot asked for help joining and nobody answered"];
  return ["Stopped", "Stopped by the user"];
}

function normalOutcome(reason: string): [string, string] {
  if (reason === "delivered_stopped") return ["Stopped", "Transcription completed; stopped by the user"];
  if (reason === "max_bot_time" || reason === "delivered_max_bot_time_exceeded") return ["Time limit reached", "The bot left at the configured time limit"];
  if (reason === "no_speech_heard") return ["No speech heard", "The bot was in the meeting but heard no one speak"];
  if (reason === "delivered_no_transcription_requested") return ["Completed", "The bot attended without transcription"];
  if (reason.startsWith("delivered_")) return ["Meeting ended", "Transcription completed"];
  return ["Meeting ended", "Everyone left the meeting"];
}

/**
 * The customer-facing label for how a run ended, from meeting-api's `outcome_class` /
 * `outcome_reason`. Only "ours" is rendered as an error; a host who did not admit the bot,
 * a user stop, or a normal end is not a failure. Returns null when the row carries no outcome
 * (older rows, in-flight states), so callers fall back to the status-based label.
 */
export function getOutcomeInfo(data?: MeetingData): (DetailedStatusInfo & { isFailure: boolean }) | null {
  const cls = data?.outcome_class;
  if (!cls || cls === "pending") return null;
  const reason = data?.outcome_reason || "";
  let label: string;
  let description: string;
  let tone: keyof typeof OUTCOME_TONE;
  switch (cls) {
    case "ours":
      [label, description, tone] = ["Bot error", "Something went wrong on our side", "error"];
      break;
    case "host_or_platform":
      [label, description] = hostOutcome(reason);
      tone = "host";
      break;
    case "user":
      [label, description] = userOutcome(reason);
      tone = "neutral";
      break;
    case "normal":
      [label, description] = normalOutcome(reason);
      tone = "ok";
      break;
    default:
      [label, description, tone] = ["Ended", "The bot run ended", "neutral"];
  }
  return { label, description, ...OUTCOME_TONE[tone], isFailure: cls === "ours" };
}

/** True when a terminal meeting should be shown as an error: outcome "ours", or no outcome and status failed. */
export function isFailureOutcome(status: MeetingStatus, data?: MeetingData): boolean {
  const o = getOutcomeInfo(data);
  return o ? o.isFailure : status === "failed";
}

export function getDetailedStatus(status: MeetingStatus, data?: MeetingData): DetailedStatusInfo {
  const baseConfig = MEETING_STATUS_CONFIG[status];

  // The graded outcome wins over the raw status for a finished run.
  if (status === "completed" || status === "failed") {
    const outcome = getOutcomeInfo(data);
    if (outcome) {
      return { label: outcome.label, description: outcome.description, color: outcome.color, bgColor: outcome.bgColor };
    }
  }

  // Fallback config in case status is invalid or config is missing
  const fallbackConfig: DetailedStatusInfo = {
    label: "Unknown",
    color: "text-gray-600 dark:text-gray-400",
    bgColor: "bg-gray-100 dark:bg-gray-800/50",
    description: "Unknown status"
  };

  // For completed meetings, check completion reason
  if (status === "completed" && data?.completion_reason) {
    switch (data.completion_reason) {
      case "stopped":
        return {
          label: "Stopped",
          color: "text-gray-600 dark:text-gray-400",
          bgColor: "bg-gray-100 dark:bg-gray-800/50",
          description: "Manually stopped by user",
        };
      case "meeting_ended":
        return {
          label: "Ended",
          color: "text-green-600 dark:text-green-400",
          bgColor: "bg-green-100 dark:bg-green-950/50",
          description: "Meeting ended naturally",
        };
      case "kicked":
      case "removed":
        return {
          label: "Removed",
          color: "text-orange-600 dark:text-orange-400",
          bgColor: "bg-orange-100 dark:bg-orange-950/50",
          description: "Bot was removed from meeting",
        };
      case "awaiting_admission_rejected":
        return {
          label: "Rejected",
          color: "text-red-600 dark:text-red-400",
          bgColor: "bg-red-100 dark:bg-red-950/50",
          description: "Bot was not admitted to meeting",
        };
      default:
        return {
          ...(baseConfig || fallbackConfig),
          color: "text-green-600 dark:text-green-400",
          bgColor: "bg-green-100 dark:bg-green-950/50",
          description: "Transcription completed"
        };
    }
  }

  // For failed meetings, add description based on error
  if (status === "failed") {
    let description = "Transcription failed";
    if (data?.error_code) {
      switch (data.error_code.toLowerCase()) {
        case "admission_timeout":
        case "not_admitted":
          description = "Bot was not admitted to meeting";
          break;
        case "meeting_ended":
          description = "Meeting ended before bot could join";
          break;
        case "connection_failed":
          description = "Failed to connect to meeting";
          break;
      }
    }
    return {
      label: "Failed",
      color: "text-red-600 dark:text-red-400",
      bgColor: "bg-red-100 dark:bg-red-950/50",
      description
    };
  }

  // For escalated meetings
  if (status === "needs_human_help") {
    return {
      label: "Needs Help",
      color: "text-orange-600 dark:text-orange-400",
      bgColor: "bg-orange-100 dark:bg-orange-950/50",
      description: data?.escalation_reason as string || "Bot is blocked and needs human intervention"
    };
  }

  // For active meetings
  if (status === "active") {
    return {
      label: "Active",
      color: "text-green-600 dark:text-green-400",
      bgColor: "bg-green-100 dark:bg-green-950/50",
      description: "Recording in progress"
    };
  }

  // For joining states
  if (status === "joining") {
    return {
      label: "Joining",
      color: "text-blue-600 dark:text-blue-400",
      bgColor: "bg-blue-100 dark:bg-blue-950/50",
      description: "Connecting to meeting"
    };
  }

  if (status === "awaiting_admission") {
    return {
      label: "Waiting",
      color: "text-amber-600 dark:text-amber-400",
      bgColor: "bg-amber-100 dark:bg-amber-950/50",
      description: "Waiting in lobby"
    };
  }

  if (status === "requested") {
    return {
      label: "Requested",
      color: "text-blue-600 dark:text-blue-400",
      bgColor: "bg-blue-100 dark:bg-blue-950/50",
      description: "Starting bot"
    };
  }

  // Return baseConfig if it exists, otherwise fallback
  return baseConfig || fallbackConfig;
}

// Languages supported by Whisper
export const SUPPORTED_LANGUAGES = [
  { code: "auto", name: "Auto-detect" },
  { code: "en", name: "English" },
  { code: "fr", name: "French" },
  { code: "de", name: "German" },
  { code: "es", name: "Spanish" },
  { code: "it", name: "Italian" },
  { code: "pt", name: "Portuguese" },
  { code: "nl", name: "Dutch" },
  { code: "pl", name: "Polish" },
  { code: "ru", name: "Russian" },
  { code: "zh", name: "Chinese" },
  { code: "ja", name: "Japanese" },
  { code: "ko", name: "Korean" },
  { code: "ar", name: "Arabic" },
  { code: "hi", name: "Hindi" },
  { code: "tr", name: "Turkish" },
  { code: "vi", name: "Vietnamese" },
  { code: "th", name: "Thai" },
  { code: "sv", name: "Swedish" },
  { code: "da", name: "Danish" },
  { code: "fi", name: "Finnish" },
  { code: "no", name: "Norwegian" },
] as const;

// ==========================================
// Recording Types (from meeting.data.recordings)
// ==========================================

export type RecordingStatus = "in_progress" | "uploading" | "completed" | "failed";
export type RecordingSource = "bot" | "upload" | "url";
export type MediaFileType = "audio" | "video" | "screenshot";

export interface RecordingMediaFile {
  id: number;
  type: MediaFileType;
  format: string; // wav, webm, opus, mp3, etc.
  storage_path: string;
  storage_backend: "minio" | "s3" | "local";
  file_size_bytes: number | null;
  duration_seconds: number | null;
  finalized_by?: string | null; // "recording_finalizer.master" when master
  is_final?: boolean;
  metadata?: Record<string, unknown>;
  created_at: string;
}

// v0.10.6.1 - canonical playback routes. Producer writes; consumer reads.
// Null sub-field means no master for that type yet.
export interface RecordingPlaybackUrl {
  audio: string | null; // stable route, e.g. /recordings/<id>/master?type=audio
  video: string | null;
}

export interface RecordingData {
  id: number;
  meeting_id: number;
  user_id: number;
  session_uid: string;
  source: RecordingSource;
  status: RecordingStatus;
  created_at: string;
  completed_at: string | null;
  media_files: RecordingMediaFile[]; // deprecated in v0.10.6.1; removed in v0.10.7
  playback_url?: RecordingPlaybackUrl | null; // v0.10.6.1 canonical
}

// ==========================================
// Admin API Types
// ==========================================

export interface VexaUser {
  id: string;
  email: string;
  name: string;
  image_url?: string;
  max_concurrent_bots: number;
  data?: Record<string, unknown>;
  created_at: string;
}

export interface VexaUserWithTokens extends VexaUser {
  api_tokens: APIToken[];
}

export interface APIToken {
  id: string;
  token: string; // Only visible once at creation
  user_id: string;
  created_at: string;
}

export interface CreateUserRequest {
  email: string;
  name?: string;
  max_concurrent_bots?: number;
}

export interface UpdateUserRequest {
  name?: string;
  max_concurrent_bots?: number;
  image_url?: string;
  data?: Record<string, unknown>;
}

export interface UsersListResponse {
  users: VexaUser[];
  total: number;
  skip: number;
  limit: number;
}

export interface CreateTokenResponse {
  id: string;
  token: string; // Save immediately - cannot be retrieved later!
  user_id: string;
  created_at: string;
}

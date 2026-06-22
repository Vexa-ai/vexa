/**
 * @vexa/dash-contracts — the single consumed-contract brick (the dashboard ↔ backend seam).
 *
 * These TS types describe ONLY what the dashboard CONSUMES, grounded verbatim on the SEALED
 * contracts in `core/gateway/contracts/`:
 *   • WS frames  → ws.v1/ws.schema.json   (the 0.10.6 live /ws multiplex truth)
 *   • REST shapes → api.v1/api.schema.json (the frozen OpenAPI 3.1 surface, "Vexa API Gateway" 1.5.0)
 *
 * The gateway forwards the raw redis payload verbatim, so data frames are type-tagged and ADDITIVE
 * (extra fields allowed). The types below mirror that: a required floor + optional carried fields,
 * and `[k: string]: unknown` index signatures where the producer adds keys we don't model.
 *
 * This brick conforms to the schemas; it never redefines them. The on-disk schemas + goldens are the
 * spec — `validate.ts` loads them and `contracts.test.ts` pins every golden against the validators.
 *
 * The `.` front door is TYPES ONLY — fully erased at compile, so a browser bundle importing
 * `@vexa/dash-contracts` carries zero runtime. The ajv validators read the sealed schemas from disk
 * (`node:fs`), so they are NODE-ONLY and live behind the separate `@vexa/dash-contracts/validate`
 * subpath, consumed by the L1/L2 tests (and injectable into the api client) — never bundled in the browser.
 */

// ════════════════════════════════════════════════════════════════════════════════════════════════
// Shared
// ════════════════════════════════════════════════════════════════════════════════════════════════

/** ws.v1 `#/$defs/MeetingRef` — the (platform, native_id) handle used to subscribe. */
export type Platform = "google_meet" | "zoom" | "teams" | "browser_session";

export interface MeetingRef {
  platform: Platform;
  native_id: string;
}

/**
 * The canonical meeting/bot status union.
 *
 * NOTE on the "needs help" state: the api.v1 `MeetingStatus` enum seals the value as
 * `needs_human_help`. The dashboard surface name requested for this brick is `needs_help`; both are
 * carried here so consumers can match either form the live `meeting.status` frame may forward
 * (the WS `payload.status` is an open `string` in ws.v1 — additive by design).
 */
export type MeetingStatus =
  | "requested"
  | "joining"
  | "awaiting_admission"
  | "active"
  | "needs_help"
  | "needs_human_help"
  | "stopping"
  | "completed"
  | "failed";

// ════════════════════════════════════════════════════════════════════════════════════════════════
// WS frames — ws.v1 (the 0.10.6 truth)
// ════════════════════════════════════════════════════════════════════════════════════════════════

/**
 * One live transcript segment as it appears inside a `transcript` bundle's confirmed/pending arrays,
 * or as the body of a `transcription_segment` frame. ws.v1 models these segment objects loosely
 * (`{ "type": "object" }`) — the collector adds speaker/timing/ids — so every field but the bundle's
 * presence is optional and extra keys are allowed.
 */
export interface TranscriptSegment {
  text?: string;
  speaker?: string | null;
  start_time?: number | string;
  end_time?: number | string;
  absolute_start_time?: string | null;
  absolute_end_time?: string | null;
  language?: string | null;
  completed?: boolean | null;
  segment_id?: string | null;
  session_uid?: string | null;
  [k: string]: unknown;
}

/** ws.v1 `#/$defs/MeetingStatus` — `meeting.status` (forwarded verbatim from bm:meeting:{id}:status). */
export interface MeetingStatusFrame {
  type: "meeting.status";
  meeting?: {
    id?: number | string;
    platform?: string | null;
    native_id?: string | null;
  };
  payload: {
    status: MeetingStatus | string;
    data?: Record<string, unknown> | null;
  };
  user_id?: number | string | null;
  ts?: string | null;
}

/** ws.v1 `#/$defs/Transcript` — the per-speaker confirmed/pending bundle (tc:meeting:{id}:mutable). */
export interface TranscriptFrame {
  type: "transcript";
  speaker?: string | null;
  meeting?: Record<string, unknown>;
  confirmed?: TranscriptSegment[];
  pending?: TranscriptSegment[];
  ts?: string | null;
}

/** ws.v1 `#/$defs/TranscriptionSegment` — a single live segment frame. type + text are the floor. */
export interface TranscriptionSegmentFrame {
  type: "transcription_segment";
  text: string;
  speaker?: string | null;
  start_time?: number | string;
  end_time?: number | string;
  language?: string | null;
  completed?: boolean | null;
  segment_id?: string | null;
  session_uid?: string | null;
  [k: string]: unknown;
}

/** ws.v1 `#/$defs/ChatMessage` — a chat message from the bot (va:meeting:{id}:chat). */
export interface ChatMessageFrame {
  type: "chat_message";
  sender?: string | null;
  text: string;
}

/** ws.v1 `#/$defs/Subscribed` — server ack of a subscribe (the meetings actually authorized). */
export interface SubscribedFrame {
  type: "subscribed";
  meetings: MeetingRef[];
}

/** ws.v1 `#/$defs/Unsubscribed` — server ack of an unsubscribe. */
export interface UnsubscribedFrame {
  type: "unsubscribed";
  meetings: MeetingRef[];
}

/** Keepalive pong. (ws.v1 keeps control frames additive; the dashboard only needs the tag.) */
export interface PongFrame {
  type: "pong";
}

/** ws.v1 `#/$defs/Error` — a protocol/auth error, `error` from the sealed code vocabulary. */
export type WsErrorCode =
  | "missing_api_key"
  | "invalid_json"
  | "invalid_subscribe_payload"
  | "invalid_unsubscribe_payload"
  | "unknown_action"
  | "authorization_service_error"
  | "authorization_call_failed";

export interface ErrorFrame {
  type: "error";
  error: WsErrorCode | string;
  details?: string | unknown[] | Record<string, unknown>;
  status?: number;
  detail?: string;
}

/** Every server → client frame the dashboard reads off `/ws`, discriminated on `type`. */
export type WsFrame =
  | MeetingStatusFrame
  | TranscriptFrame
  | TranscriptionSegmentFrame
  | ChatMessageFrame
  | SubscribedFrame
  | UnsubscribedFrame
  | PongFrame
  | ErrorFrame;

// ════════════════════════════════════════════════════════════════════════════════════════════════
// REST shapes — api.v1 (sealed OpenAPI 3.1, "Vexa API Gateway" 1.5.0)
// ════════════════════════════════════════════════════════════════════════════════════════════════

/** api.v1 `#/components/schemas/MeetingResponse` — GET /meetings/{id}, an item of MeetingListResponse. */
export interface MeetingResponse {
  id: number;
  user_id: number;
  platform?: string | null;
  native_meeting_id?: string | null;
  constructed_meeting_url?: string | null;
  status: MeetingStatus | string;
  bot_container_id: string | null;
  start_time: string | null;
  end_time: string | null;
  completion_reason?: string | null;
  failure_stage?: string | null;
  data?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

/** api.v1 `#/components/schemas/MeetingListResponse` — GET /meetings. */
export interface MeetingListResponse {
  meetings: MeetingResponse[];
}

/** api.v1 `#/components/schemas/TranscriptionSegment` — a REST transcript segment (start/end seconds). */
export interface TranscriptionSegment {
  start: number;
  end: number;
  text: string;
  language: string | null;
  created_at?: string | null;
  speaker?: string | null;
  completed?: boolean | null;
  absolute_start_time?: string | null;
  absolute_end_time?: string | null;
  segment_id?: string | null;
}

/** api.v1 `#/components/schemas/TranscriptionResponse` — GET /transcripts/{platform}/{native_id}. */
export interface TranscriptionResponse {
  id: number;
  platform: Platform;
  native_meeting_id: string | null;
  constructed_meeting_url: string | null;
  status: string;
  start_time: string | null;
  end_time: string | null;
  recordings?: Array<Record<string, unknown>>;
  notes?: string | null;
  data?: Record<string, unknown> | null;
  segments: TranscriptionSegment[];
}

/**
 * The `GET /recordings/{recording_id}/master` shape the dashboard consumes.
 *
 * This is NOT a sealed api.v1 component (the OpenAPI doc seals `GET /recordings` +
 * `GET /recordings/{recording_id}` but not the `/master` projection), so it lives here as the
 * dashboard's typed read of the recording-master record the player needs.
 */
export interface RecordingMaster {
  id: number | string;
  type: string;
  storage_path: string;
  media_file_id: number | string;
  raw_url: string;
  duration_seconds: number;
}

/**
 * The api.v1 REST PORT — the one interface the dashboard talks to.
 *
 * Every method returns a @vexa/dash-contracts shape (the consumed api.v1 surface). The dashboard
 * depends on THIS interface, never on `fetch` directly: `createHttpApiClient` (real backend) and
 * `createFakeApiClient` (in-memory goldens) are the two implementations, swappable behind the port.
 *
 * Paths (sealed api.v1, "Vexa API Gateway" 1.5.0):
 *   getMeetings()          → GET    /meetings
 *   getMeeting(id)         → GET    /meetings/{meeting_id}
 *   getTranscripts(p, n)   → GET    /transcripts/{platform}/{native_meeting_id}
 *   getRecordingMaster(id) → GET    /recordings/{recording_id}/master
 *   postBot(req)           → POST   /bots
 *   deleteBot(p, n)        → DELETE /bots/{platform}/{native_meeting_id}
 */
import type {
  Platform,
  MeetingListResponse,
  MeetingResponse,
  TranscriptionResponse,
  RecordingMaster,
} from "@vexa/dash-contracts";

/**
 * GET /meetings query params (all optional). The sealed endpoint takes no params today; the dashboard
 * carries client-side filters here additively so the port stays stable as the backend grows.
 */
export interface GetMeetingsParams {
  status?: string;
  platform?: Platform | string;
  [k: string]: unknown;
}

/**
 * POST /bots request body. `platform` + `native_meeting_id` are the meeting handle the dashboard
 * sends; the rest are optional per-meeting overrides (the api.v1 body seals no required field). Extra
 * keys are allowed — the body is additive.
 */
export interface BotRequest {
  platform: Platform | string;
  native_meeting_id?: string;
  bot_name?: string;
  language?: string;
  task?: string;
  meeting_url?: string;
  recording_enabled?: boolean;
  transcribe_enabled?: boolean;
  [k: string]: unknown;
}

/** The recording-master `type` selector (e.g. the mixed vs gmeet master projection). */
export type RecordingMasterType = string;

/** The single REST seam the dashboard consumes. */
export interface ApiClient {
  getMeetings(params?: GetMeetingsParams): Promise<MeetingListResponse>;
  getMeeting(id: number | string): Promise<MeetingResponse>;
  getTranscripts(platform: Platform | string, nativeId: string): Promise<TranscriptionResponse>;
  getRecordingMaster(
    recordingId: number | string,
    type?: RecordingMasterType,
  ): Promise<RecordingMaster>;
  postBot(req: BotRequest): Promise<MeetingResponse>;
  deleteBot(platform: Platform | string, nativeId: string): Promise<void>;
}

/** The minimal `fetch` slice this brick uses, so tests can inject a stub without DOM lib types. */
export type FetchImpl = (
  input: string,
  init?: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
  },
) => Promise<FetchResponse>;

/** The minimal `Response` slice this brick reads. */
export interface FetchResponse {
  ok: boolean;
  status: number;
  statusText?: string;
  json(): Promise<unknown>;
  text(): Promise<string>;
}

import type { MeetingLanguage } from "./transcriptionLanguage";

/** meetingModel — the meeting/transcript/entity SHAPES the live path works in. Real types only; the
 *  backend (meeting-api via the gateway) fills them. No fixtures, no fallbacks. */

export interface Participant { name: string; role: string; initials: string; /** the invite's address, when the calendar gave one — what opens their profile */ email?: string }
/** A calendar-invited human (data.attendees, prep-v3 slice b) — email is the identity key. */
export interface Attendee { email: string; name?: string; partstat?: string }
export interface ProposedAction { id: string; label: string; detail: string }
export interface TranscriptLine { offsetSeconds?: number; tsMs?: number; endMs?: number; t: string; speaker: string; text: string; id?: string }
export interface MeetingMock {
  id: string;
  session_uid?: string;       // set on a LIVE-backend meeting → the tab subscribes to the real Stream
  native_id?: string;         // the native Meet code (real meetings) — used to stop / re-send the bot
  has_recording?: boolean;    // a past meeting with a recording (opens the recorded view)
  title: string;
  when: string;
  status: "live" | "past";
  live_status?: string;       // the RAW meeting-api status — drives the status badge + action dropdown
  shared?: boolean;           // surfaced via a share/membership (not owned by the caller) — badged in the list
  shared_with?: number;       // the OWNER's view: how many people this meeting is shared with ("Shared with N")
  scheduled_at?: string;      // when a `scheduled` meeting is due (data.scheduled_at)
  start_time?: string;        // when the run actually started (row start_time) — sorts recordings
  end_time?: string;          // when the run ended (row end_time) — with start_time gives duration
  created_at?: string;        // when the row was made (row created_at) — the time of a call that never started
  title_custom?: string;      // the user-given planned-meeting title (data.title) — wins over the fallback
  workspace_id?: string;      // the sharing bind (data.workspace_id) — members of it see this meeting
  calendar_uid?: string;      // calendar-import provenance (data.calendar_uid)
  attendees?: Attendee[];     // invited humans from the calendar (data.attendees)
  auto_join?: boolean;        // "scheduled means the bot joins" toggle (data.auto_join; absent = on)
  auto_join_error?: string;   // the auto-join sweep's LOUD failure (data.auto_join_error)
  meeting_url?: string;       // the joinable link (constructed_meeting_url) — send-bot uses it verbatim
  platform: string;
  participants: Participant[];
  mentioned: string[];          // workspace entity titles surfaced from the conversation
  actions: ProposedAction[];
  transcript: TranscriptLine[];
  insights: { t: string; text: string }[];  // copilot notes, revealed alongside the transcript
  docs?: { workspace: string; path: string; title?: string; kind?: string }[];  // connected workspace docs (data.docs)
  artifacts_deleted?: boolean;  // its owner deleted the transcript + recordings (data.artifact_deletion)
  transcription_language?: MeetingLanguage;  // the language setting the bot runs with (data.transcription_language)
  room?: string;                // the room (its session id) a call started in, which owns it (data.metadata.room)
  // A VEXA CALL (founder O1, 2026-10-18) — see liveMeetings `callFields`
  call_session?: string;        // the session (s-…) the call is held in (data.session_id)
  call_room?: string;           // the room it belongs to, when it was started in one (data.metadata.room)
  completion_reason?: string;   // why it ended (data.completion_reason): `left_alone` = everyone left
  renamed_from?: string;        // its who-or-where name before the topic renamed it (data.metadata.call_renamed_from)
  call_with?: { subject: string; name: string }[];  // a call with one person: both of them
}

// ── meeting lifecycle phase (design-spec meeting-lifecycle-v2) ──────────────────────
// The ONE phase source for the meeting page, the Today tab, and the chat mode chip.
// prep = user intent, nothing captured yet · live = bot in/heading-to the room · post = ran and ended.
export type MeetingPhase = "prep" | "live" | "post";
const PREP_STATUSES = new Set(["idle", "scheduled"]);
const LIVE_PHASE_STATUSES = new Set(["active", "joining", "requested", "awaiting_admission", "needs_help", "stopping"]);

export function meetingPhase(m: Pick<MeetingMock, "live_status" | "status">): MeetingPhase {
  const s = m.live_status ?? "";
  if (PREP_STATUSES.has(s)) return "prep";
  if (LIVE_PHASE_STATUSES.has(s)) return "live";
  if (s) return "post";                            // completed/failed/stopped/unknown-terminal
  return m.status === "live" ? "live" : "post";    // no raw status → coarse bucket decides
}

export type EntityType = "person" | "company" | "topic" | "task";
export interface Entity {
  title: string;
  type: EntityType;
  path: string;            // workspace path
  exists: boolean;         // already a file in the workspace?
  subtitle: string;        // role / one-liner
  facts?: [string, string][];
  summary?: string;
  related?: string[];      // [[linked]] entity titles
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/** A default entity scaffold for a title surfaced in a meeting (no fixture lookup). */
export function entityFor(title: string): Entity {
  return { title, type: "topic", path: `kg/entities/topic/${slug(title)}.md`, exists: false, subtitle: "Topic" };
}

/** Split a meeting's people + mentioned entities into "in the room" vs "detected" (deduped). */
export function meetingEntities(m: MeetingMock): { present: Entity[]; detected: Entity[] } {
  const present = m.participants.map((p) => entityFor(p.name));
  const seen = new Set(present.map((e) => e.title));
  const detected = m.mentioned.filter((t) => !seen.has(t)).map(entityFor);
  return { present, detected };
}

/** F169 — "the panel stays on the ended meeting". A bot can be RE-DROPPED into the SAME call (the
 *  person says "send bot" again after the first session ended, or an auto-reconnect fires):
 *  meeting-api mints a NEW row (a new `id`) for the new session, but the underlying call's
 *  `native_id` (the Meet/Zoom code) is unchanged. A tab pinned to the OLD row's id used to keep
 *  rendering that row's now-static transcript forever — `resolveMeeting` only ever looked up the
 *  exact id it was given, so the pane never learned a fresher session for the same call existed.
 *
 *  Pure, no I/O — the meeting pane (`canvas/useMeeting`) and the chat (`focusedMeeting`) both resolve
 *  a row through it: given the resolved row and the full list, is there a LIVE row for the same call,
 *  newer than this one? Row ids are
 *  sequential ints assigned in creation order (matchesMeeting/toMock's own comment), so the
 *  highest numeric id among same-`native_id` live rows is unambiguously the newest session —
 *  robust even when a fresh row's `start_time` hasn't landed yet (still `requested`/`joining`). */
export function newerActiveSameCall(meetings: MeetingMock[], resolved: MeetingMock): MeetingMock | undefined {
  if (resolved.status === "live" || !resolved.native_id) return undefined;
  const candidates = meetings.filter(
    (m) => m.status === "live" && m.native_id === resolved.native_id && m.id !== resolved.id,
  );
  if (!candidates.length) return undefined;
  return candidates.reduce((newest, m) => (Number(m.id) > Number(newest.id) ? m : newest));
}

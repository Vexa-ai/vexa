/** meetingModel — the meeting/transcript/entity SHAPES the live path works in. Real types only; the
 *  backend (meeting-api via the gateway) fills them. No fixtures, no fallbacks. */

export interface Participant { name: string; role: string; initials: string }
export interface ProposedAction { id: string; label: string; detail: string }
export interface TranscriptLine { t: string; speaker: string; text: string }
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
  scheduled_at?: string;      // when a `scheduled` meeting is due (data.scheduled_at)
  title_custom?: string;      // the user-given planned-meeting title (data.title) — wins over the fallback
  workspace_id?: string;      // the sharing bind (data.workspace_id) — members of it see this meeting
  calendar_uid?: string;      // calendar-import provenance (data.calendar_uid)
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

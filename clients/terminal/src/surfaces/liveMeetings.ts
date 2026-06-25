"use client";
/** meetings feed — the terminal's REAL meetings list (live AND past), sourced from meeting-api via the
 *  gateway: `GET /api/meetings` → gateway → meeting-api `GET /meetings`. Each row is shaped
 *  {id, platform, native_meeting_id, status, start_time, end_time, data:{recordings:[...]}}, newest-first.
 *  Live meetings carry a `session_uid` so the tab subscribes to the copilot stream; past meetings open a
 *  recorded view whose transcript is fetched on demand from `GET /api/transcripts/{platform}/{native}`. */
import { useSyncExternalStore } from "react";
import type { MeetingMock, TranscriptLine } from "./mock";
import { onMeetingStatus } from "./gatewayWS";

/** A row from meeting-api GET /meetings (live AND past). */
interface MeetingRowDTO {
  id: number | string;
  platform: string;
  native_meeting_id: string;
  status: string;
  start_time?: string | null;
  end_time?: string | null;
  data?: { recordings?: unknown[]; docs?: { workspace: string; path: string; title?: string; kind?: string }[]; scheduled_at?: string; stop_requested?: boolean } | null;
}

/** `stopped` is not a DB enum value — it's derived from a terminal `completed` row that the user stopped
 *  (data.stop_requested, per the design doc §A). Resolve the display status from the raw row. */
function displayStatus(d: MeetingRowDTO): string {
  if (d.status === "completed" && d.data?.stop_requested) return "stopped";
  return d.status;
}

/** A transcript segment from meeting-api GET /transcripts/{platform}/{native}. */
interface SegmentDTO {
  start?: number | null;
  speaker?: string | null;
  text?: string | null;
}

// Statuses where the bot is in/heading-to the room — these map to the list's "live" bucket and carry a
// session_uid so the tab subscribes to the copilot stream. awaiting_admission/needs_help are live too.
const LIVE_STATUSES = new Set(["active", "joining", "requested", "awaiting_admission", "needs_help", "stopping"]);

let meetings: MeetingMock[] = [];
const subs = new Set<() => void>();
let started = false;
let wsUnsub: (() => void) | null = null;

function whenLabel(d: MeetingRowDTO, live: boolean): string {
  if (live) return "Now · live";
  if (!d.start_time) return "Recorded";
  try { return new Date(d.start_time).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return "Recorded"; }
}

function toMock(d: MeetingRowDTO): MeetingMock {
  const raw = displayStatus(d);
  const live = LIVE_STATUSES.has(d.status);
  const native = d.native_meeting_id;
  return {
    id: native,
    native_id: native,
    session_uid: live ? native : undefined,  // only live meetings subscribe to the copilot stream
    title: `${d.platform === "google_meet" ? "Google Meet" : d.platform} · ${native}`,
    when: whenLabel(d, live),
    status: live ? "live" : "past",
    live_status: raw,
    scheduled_at: d.data?.scheduled_at ?? undefined,
    platform: d.platform === "google_meet" ? "Google Meet" : d.platform,
    has_recording: !!(d.data?.recordings?.length),
    docs: d.data?.docs ?? [],
    participants: [],
    mentioned: [],
    actions: [],
    transcript: [],
    insights: [],
  };
}

/** ONE snapshot fetch of the real meetings list (gateway → meeting-api). Seeds / re-seeds the store; the
 *  live deltas thereafter arrive over the WebSocket. Called once on mount and on each (re)connect. */
async function snapshot() {
  try {
    const r = await fetch("/api/meetings", { cache: "no-store" });
    const { meetings: list } = (await r.json()) as { meetings: MeetingRowDTO[] };
    // meeting-api returns one row per bot-launch; the same Meet relaunched yields several rows with the
    // same native code. Dedupe to ONE row per native (newest wins — the list is newest-first).
    const seen = new Set<string>();
    const next = (list || []).map(toMock).filter((m) => !seen.has(m.id) && (seen.add(m.id), true));
    const key = (m: MeetingMock[]) => m.map((x) => `${x.id}|${x.live_status}|${x.has_recording}`).join(",");
    if (key(next) !== key(meetings)) {
      meetings = next;
      subs.forEach((f) => f());
    }
  } catch {
    /* offline — keep last known */
  }
}

/** Apply a `meeting.status` WS frame to the store: patch the matching row's status in place (the snapshot
 *  already seeded the row metadata). Match by native, falling back to meeting_id. Unknown rows trigger a
 *  re-snapshot so a freshly-created (scheduled/idle) meeting surfaces. */
function applyFrame(f: { meeting_id?: number | string; native?: string; status: string; when?: string }) {
  const i = meetings.findIndex(
    (m) => (f.native && m.native_id === f.native) || (f.meeting_id != null && m.id === String(f.meeting_id)),
  );
  if (i < 0) { void snapshot(); return; }
  const live = LIVE_STATUSES.has(f.status);
  const cur = meetings[i];
  const nextRow: MeetingMock = {
    ...cur,
    live_status: f.status,
    status: live ? "live" : "past",
    session_uid: live ? cur.native_id : undefined,
    scheduled_at: f.status === "scheduled" ? (f.when ?? cur.scheduled_at) : cur.scheduled_at,
  };
  meetings = [...meetings.slice(0, i), nextRow, ...meetings.slice(i + 1)];
  subs.forEach((fn) => fn());
}

function ensureStarted() {
  if (started || typeof window === "undefined") return;
  started = true;
  void snapshot();                          // initial snapshot on mount
  wsUnsub = onMeetingStatus(applyFrame);    // then live status deltas over the gateway WS
}

/** Fetch a PAST meeting's recorded transcript over REST (gateway → meeting-api). Maps each segment to a
 *  TranscriptLine for the transcript pane. Returns [] on error / no transcript. */
export async function fetchTranscript(platform: string, nativeId: string): Promise<TranscriptLine[]> {
  // the platform on the mock is display-cased ("Google Meet") — normalise back to the API slug
  const slug = platform === "Google Meet" ? "google_meet" : platform.toLowerCase().replace(/\s+/g, "_");
  const fmt = (t?: number | null) =>
    t == null ? "" : `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  try {
    const r = await fetch(`/api/transcripts/${slug}/${encodeURIComponent(nativeId)}`, { cache: "no-store" });
    if (!r.ok) return [];
    const { segments } = (await r.json()) as { segments?: SegmentDTO[] };
    return (segments || [])
      .filter((s) => (s.text ?? "").trim())
      .map((s) => ({ t: fmt(s.start), speaker: s.speaker || "Speaker", text: s.text ?? "" }));
  } catch {
    return [];
  }
}

/** Last-known meeting by id (sync) — lets non-hook lookups resolve a real meeting. */
export function getLiveMeeting(id: string): MeetingMock | undefined {
  return meetings.find((m) => m.id === id);
}

/** All last-known real meetings (sync) — used by the auto-open command (prefers a live one). */
export function liveMeetingsNow(): MeetingMock[] {
  return meetings;
}

/** Force a one-shot snapshot re-fetch — call after a dropdown action (schedule/cancel/send/stop) so the
 *  list reflects the new status immediately, even before the echoing WS frame lands. */
export function refreshMeetings(): void {
  void snapshot();
}

/** Subscribe a component to the meetings feed (live + past). */
export function useLiveMeetings(): MeetingMock[] {
  ensureStarted();
  return useSyncExternalStore(
    (cb) => { subs.add(cb); return () => subs.delete(cb); },
    () => meetings,
    () => meetings,
  );
}

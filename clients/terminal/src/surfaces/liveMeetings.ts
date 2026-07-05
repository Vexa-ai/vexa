"use client";
/** meetings feed — the terminal's REAL meetings list (live AND past), sourced from meeting-api via the
 *  gateway: `GET /api/meetings` → gateway → meeting-api `GET /meetings`. Each row is shaped
 *  {id, platform, native_meeting_id, status, start_time, end_time, data:{recordings:[...]}}, newest-first.
 *  Live meetings carry a `session_uid` so the tab subscribes to the copilot stream; past meetings open a
 *  recorded view whose transcript is fetched on demand from `GET /api/transcripts/{platform}/{native}`. */
import { useSyncExternalStore } from "react";
import type { MeetingMock, TranscriptLine } from "./meetingModel";
import { onGatewayWSConnected, onMeetingStatus } from "./gatewayWS";

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

/** A persisted processed note from the durable store (`data.processed.views[].doc.notes[]`,
 *  written by meeting-api's db-writer from the copilot's proc stream). SAME producer and shape as
 *  the live SSE `note` event payload — {id, speaker, chapter, text, t?, pass, frozen}. */
export interface ProcessedNoteDTO {
  id: string;
  speaker?: string;
  chapter?: string;
  text: string;
  t?: number;
  tsMs?: number;   // absent in the durable store (live-only anchor); optional so the merged union renders
  pass?: number;
  frozen?: boolean;
}

/** The copilot view id inside data.processed.views[] (mirrors meeting-api's PROC_VIEW_ID). */
const COPILOT_NOTES_VIEW_ID = "copilot-notes";

interface ProcessedViewDTO { id?: string; doc?: { notes?: unknown[] } | null }
interface TranscriptResponseDTO {
  segments?: SegmentDTO[];
  data?: { processed?: { views?: ProcessedViewDTO[] } | null } | null;
}

/** Both durable halves of a meeting's transcript response: the raw segments (mapped for the
 *  transcript pane) and the copilot's persisted processed notes. */
export interface DurableTranscript {
  lines: TranscriptLine[];
  notes: ProcessedNoteDTO[];
}

/** Pull the copilot-notes view's notes out of a transcript response body. Exported for tests. */
export function processedNotesOf(body: TranscriptResponseDTO | null | undefined): ProcessedNoteDTO[] {
  const views = body?.data?.processed?.views;
  if (!Array.isArray(views)) return [];
  const view = views.find((v) => v?.id === COPILOT_NOTES_VIEW_ID);
  const raw = view?.doc?.notes;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((n): n is Record<string, unknown> => !!n && typeof n === "object")
    .map((n) => ({
      id: String(n.id ?? "").trim(),
      speaker: typeof n.speaker === "string" ? n.speaker : undefined,
      chapter: typeof n.chapter === "string" ? n.chapter : undefined,
      text: typeof n.text === "string" ? n.text : "",
      t: typeof n.t === "number" && Number.isFinite(n.t) ? n.t : undefined,
      pass: typeof n.pass === "number" ? n.pass : undefined,
      frozen: typeof n.frozen === "boolean" ? n.frozen : undefined,
    }))
    .filter((n) => n.id && n.text.trim());
}

/** Merge live note deltas OVER a durable seed by note id (the backend's own merge rule — a live
 *  re-emit of a persisted note updates it in place, never duplicates). Seed order is preserved;
 *  notes only seen live append in arrival order. Exported for tests. */
export function mergeNotesById<T extends { id: string }>(seed: T[], live: T[]): T[] {
  if (!seed.length) return live;
  if (!live.length) return seed;
  const seedIds = new Set(seed.map((n) => n.id));
  const liveById = new Map(live.map((n) => [n.id, n]));
  const out: T[] = seed.map((n) => liveById.get(n.id) ?? n);
  for (const n of live) if (!seedIds.has(n.id)) out.push(n);
  return out;
}

function formatTranscriptTime(start?: number | null): string {
  if (start == null || !Number.isFinite(start)) return "";
  const date = new Date(start * 1000);
  if (!Number.isFinite(date.getTime())) return "";
  return date.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

// Statuses where the bot is in/heading-to the room — these map to the list's "live" bucket and carry a
// session_uid so the tab subscribes to the copilot stream. awaiting_admission/needs_help are live too.
const LIVE_STATUSES = new Set(["active", "joining", "requested", "awaiting_admission", "needs_help", "stopping"]);

let meetings: MeetingMock[] = [];
const subs = new Set<() => void>();
let started = false;
let wsUnsub: (() => void) | null = null;
let connUnsub: (() => void) | null = null;
let storeRevision = 0;

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
  const revision = ++storeRevision;
  try {
    const r = await fetch("/api/meetings", { cache: "no-store" });
    const { meetings: list } = (await r.json()) as { meetings: MeetingRowDTO[] };
    if (revision !== storeRevision) return;
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
  storeRevision += 1;
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
  connUnsub = onGatewayWSConnected((ok) => {
    if (ok) void snapshot();
  });
}

const EMPTY_DURABLE: DurableTranscript = { lines: [], notes: [] };

/** Fetch a meeting's DURABLE transcript over REST (gateway → meeting-api): the recorded segments
 *  for the transcript pane PLUS the copilot's persisted processed notes (data.processed.views —
 *  the copilot-notes view). For a past meeting this is THE source; for a live one it seeds
 *  whatever was persisted before the client connected. Returns empties on error. */
export async function fetchDurableTranscript(platform: string, nativeId: string): Promise<DurableTranscript> {
  // the platform on the mock is display-cased ("Google Meet") — normalise back to the API slug
  const slug = platform === "Google Meet" ? "google_meet" : platform.toLowerCase().replace(/\s+/g, "_");
  try {
    const r = await fetch(`/api/transcripts/${slug}/${encodeURIComponent(nativeId)}`, { cache: "no-store" });
    if (!r.ok) return EMPTY_DURABLE;
    const body = (await r.json()) as TranscriptResponseDTO;
    const list = body.segments || [];
    const lines = list
      .filter((s) => (s.text ?? "").trim())
      .map((s) => ({ t: formatTranscriptTime(s.start), speaker: s.speaker || "Speaker", text: s.text ?? "" }));
    return { lines, notes: processedNotesOf(body) };
  } catch {
    return EMPTY_DURABLE;
  }
}

/** Fetch a PAST meeting's recorded transcript lines (segments only). Kept for callers that don't
 *  need the processed notes. */
export async function fetchTranscript(platform: string, nativeId: string): Promise<TranscriptLine[]> {
  return (await fetchDurableTranscript(platform, nativeId)).lines;
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

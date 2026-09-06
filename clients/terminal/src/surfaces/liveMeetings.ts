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
  native_meeting_id: string | null;   // null on a link-less PLANNED meeting (platform 'unknown')
  status: string;
  shared?: boolean;   // surfaced via a share/membership (not owned by the caller)
  start_time?: string | null;
  end_time?: string | null;
  constructed_meeting_url?: string | null;
  data?: {
    recordings?: unknown[];
    docs?: { workspace: string; path: string; title?: string; kind?: string }[];
    scheduled_at?: string;
    stop_requested?: boolean;
    // planned-meeting keys (POST /meetings / calendar sync)
    title?: string;
    workspace_id?: string;
    calendar_uid?: string;
    auto_join?: boolean;
    auto_join_error?: string;
    constructed_meeting_url?: string;
    attendees?: { email: string; name?: string; partstat?: string }[];
  } | null;
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

interface TranscriptResponseDTO {
  segments?: SegmentDTO[];
}

/** The durable half of a meeting's transcript response: the raw recorded segments, mapped for the
 *  transcript pane. There is no second, "processed" body — PRD decision 34 removed the in-product
 *  inference pipeline that produced it, so `data.processed` has no producer and is not read. */
export interface DurableTranscript {
  lines: TranscriptLine[];
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
let loaded = false;        // a snapshot has come back at least once — an id absent from the list is then
                           // genuinely unknown (not merely not-fetched-yet), which is what lets a meeting
                           // route render a not-found state instead of a forever-"Connecting…" shell.
let wsConnected = false;   // the live meeting.status stream's connection state — part of the store's external state
const subs = new Set<() => void>();
let started = false;
let wsUnsub: (() => void) | null = null;
let connUnsub: (() => void) | null = null;
let storeRevision = 0;

function whenLabel(d: MeetingRowDTO, live: boolean): string {
  if (live) return "Now · live";
  // a PLANNED meeting's row shows its planned time, not "Recorded"
  if ((d.status === "scheduled" || d.status === "idle") && !d.start_time) {
    const at = d.data?.scheduled_at;
    if (!at) return "No time set";
    try { return new Date(at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
    catch { return "Scheduled"; }
  }
  if (!d.start_time) return "Recorded";
  try { return new Date(d.start_time).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return "Recorded"; }
}

function toMock(d: MeetingRowDTO): MeetingMock {
  const raw = displayStatus(d);
  const live = LIVE_STATUSES.has(d.status);
  const native = d.native_meeting_id;
  // P0 (cross-tenant leak + wrong-row hydration fix): the tab identity + the live SUBSCRIBE key is the
  // meetings-domain ROW id (`d.id`), NOT the native code. The native id is NOT unique — it collides
  // across a user's re-sends of the same link (distinct rows) and across DIFFERENT tenants. Keying the
  // tab/subscribe by the row id makes every row a DISTINCT meeting: it subscribes to its OWN row-keyed
  // transcript stream (`tc:meeting:{id}`) and its OWN copilot out-stream (`agent-meet-{id}`), and fetches
  // its OWN durable transcript by id. The native id rides on `native_id` for DISPLAY + bot actions
  // (send/stop target the native), and the readable meeting-doc name.
  const id = String(d.id);
  return {
    id,
    native_id: native ?? undefined,
    session_uid: live ? id : undefined,  // only live meetings subscribe to the copilot stream — by ROW id
    // a planned meeting's user-given title wins; otherwise the platform·native fallback
    // Honest fallbacks (design-spec W3): a link-less plan is "Untitled meeting", never
    // "unknown · (no link)"; a linked one reads "Google Meet · abc-defg-hij".
    title: d.data?.title
      || (native ? `${d.platform === "google_meet" ? "Google Meet" : d.platform} · ${native}` : "Untitled meeting"),
    title_custom: d.data?.title ?? undefined,
    when: whenLabel(d, live),
    status: live ? "live" : "past",
    live_status: raw,
    shared: !!d.shared,   // owned by someone else, surfaced via a share/membership (data.shared)
    scheduled_at: d.data?.scheduled_at ?? undefined,
    workspace_id: d.data?.workspace_id ?? undefined,
    calendar_uid: d.data?.calendar_uid ?? undefined,
    attendees: d.data?.attendees ?? undefined,
    auto_join: d.data?.auto_join,
    auto_join_error: d.data?.auto_join_error ?? undefined,
    meeting_url: d.constructed_meeting_url ?? d.data?.constructed_meeting_url ?? undefined,
    start_time: d.start_time ?? undefined,
    end_time: d.end_time ?? undefined,
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
/** Coalesce snapshot bursts. `snapshot()` is fired on mount, on every WS (re)connect, and by every
 *  status frame naming a row the store does not hold — so anything that repeats upstream becomes a
 *  request storm here. It did: a gateway close-loop on 2026-09-02 turned one idle browser into 519
 *  `GET /api/meetings` calls in three minutes, and a store notification on every connectedness
 *  flip, which flickered the rail under the founder while he was using it.
 *
 *  The backoff bug that drove that loop is fixed in `gatewayWS.ts`, but this is the layer that
 *  AMPLIFIED it, and it should not amplify the next one either. One in-flight snapshot at a time;
 *  a call arriving while one is running sets a trailing flag and re-runs once when it lands, so a
 *  burst of N collapses to at most 2 requests and never loses the final state. */
let snapInFlight = false;
let snapPending = false;

async function snapshot(): Promise<void> {
  if (snapInFlight) { snapPending = true; return; }
  snapInFlight = true;
  try {
    await snapshotOnce();
  } finally {
    snapInFlight = false;
    if (snapPending) { snapPending = false; void snapshot(); }
  }
}

async function snapshotOnce() {
  const revision = ++storeRevision;
  try {
    const r = await fetch("/api/meetings", { cache: "no-store" });
    const { meetings: list } = (await r.json()) as { meetings: MeetingRowDTO[] };
    if (revision !== storeRevision) return;
    // P0: meeting-api returns one row per bot-launch. Each row is a DISTINCT meeting run (its own
    // transcript/processed doc), so we keep them ALL — no longer collapsed to one row per native (that
    // collapse hydrated the wrong row's notes). Dedup is keyed by the ROW id purely to defend against a
    // duplicated row in the list (idempotent), never to merge distinct rows sharing a native.
    const seen = new Set<string>();
    const next = (list || []).map(toMock).filter((m) => !seen.has(m.id) && (seen.add(m.id), true));
    const key = (m: MeetingMock[]) => m.map((x) =>
      `${x.id}|${x.live_status}|${x.has_recording}|${x.title_custom ?? ""}|${x.scheduled_at ?? ""}|${x.workspace_id ?? ""}|${x.auto_join ?? ""}|${x.auto_join_error ?? ""}|${x.native_id ?? ""}|${(x.attendees ?? []).map((a) => a.email).join("+")}`,
    ).join(",");
    const wasLoaded = loaded;
    loaded = true;
    if (!wasLoaded || key(next) !== key(meetings)) {
      meetings = next;
      subs.forEach((f) => f());
    }
  } catch {
    /* offline — keep last known, and stay UNLOADED: an unreachable list must not make every meeting
       look deleted. The view keeps resolving until a snapshot actually answers. */
  }
}

/** Apply a `meeting.status` WS frame to the store: patch the matching row's status in place (the snapshot
 *  already seeded the row metadata). Match by native, falling back to meeting_id. Unknown rows trigger a
 *  re-snapshot so a freshly-created (scheduled/idle) meeting surfaces. */
function applyFrame(f: { meeting_id?: number | string; native?: string; status: string; when?: string }) {
  storeRevision += 1;
  // P0: match the ROW id first (`meeting_id`) — a native-only match would patch EVERY row sharing that
  // native (several distinct meetings), flipping the wrong rows' status. Fall back to native only when
  // the frame carries no row id (older producer), accepting that ambiguity for that legacy frame shape.
  const i = meetings.findIndex(
    (m) => (f.meeting_id != null && m.id === String(f.meeting_id)) || (f.native != null && f.meeting_id == null && m.native_id === f.native),
  );
  if (i < 0) { void snapshot(); return; }
  // a DELETED row (calendar sync retiring a planned meeting) leaves the store — patching it in
  // place made a cancelled future meeting masquerade as "Recorded" until the next snapshot
  if (f.status === "deleted") {
    meetings = [...meetings.slice(0, i), ...meetings.slice(i + 1)];
    subs.forEach((fn) => fn());
    return;
  }
  const live = LIVE_STATUSES.has(f.status);
  const cur = meetings[i];
  const nextRow: MeetingMock = {
    ...cur,
    live_status: f.status,
    status: live ? "live" : "past",
    session_uid: live ? cur.id : undefined,  // subscribe by the ROW id (P0)
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
    // Propagate connected-ness into the store's external state: consumers (the meeting header's
    // bot controls) must know when the rows are a possibly-stale snapshot rather than live truth
    // (issue #674) — a disconnected store never silently serves stale rows as current.
    if (wsConnected !== ok) {
      wsConnected = ok;
      subs.forEach((f) => f());
    }
    if (ok) void snapshot();
  });
}

const EMPTY_DURABLE: DurableTranscript = { lines: [] };

/** Fetch a meeting's DURABLE transcript over REST (gateway → meeting-api): the recorded segments
 *  for the transcript pane. For a past meeting this is THE source; for a live one it seeds
 *  whatever was persisted before the client connected. Returns empties on error.
 *
 *  P0 (wrong-row hydration fix): fetch by the meetings-domain ROW id via
 *  `GET /api/transcripts/by-id/{meetingId}` (owner-scoped downstream). The native path
 *  (`/transcripts/{platform}/{native}`) resolves to the NEWEST row for that native, so a user with
 *  several rows on the same link always read the latest — an OLDER row's segments vanished. Fetching
 *  by the exact row id returns THAT row's segments, never a sibling's (and never
 *  another tenant's). `meetingId` is the row id the mock now carries as `id`. */
export async function fetchDurableTranscript(meetingId: string): Promise<DurableTranscript> {
  try {
    const r = await fetch(`/api/transcripts/by-id/${encodeURIComponent(meetingId)}`, { cache: "no-store" });
    if (!r.ok) return EMPTY_DURABLE;
    const body = (await r.json()) as TranscriptResponseDTO;
    const list = body.segments || [];
    const lines = list
      .filter((s) => (s.text ?? "").trim())
      .map((s) => ({ t: formatTranscriptTime(s.start), speaker: s.speaker || "Speaker", text: s.text ?? "" }));
    return { lines };
  } catch {
    return EMPTY_DURABLE;
  }
}

/** Fetch a PAST meeting's recorded transcript lines by the ROW id. */
export async function fetchTranscript(meetingId: string): Promise<TranscriptLine[]> {
  return (await fetchDurableTranscript(meetingId)).lines;
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

/** BIND-TIME REPAIR — the id that arrives before the row does.
 *
 *  A meeting row created MID-SESSION (the chat sends a bot; meeting-api makes row 132) does not
 *  enter this store until something re-snapshots. `applyFrame` re-snapshots on a `meeting.status`
 *  frame naming an unknown row, but that frame either does not fire on creation or races the
 *  subscription — so whatever binds the new id first (a chat's `meeting`, a `meeting:` artifact, a
 *  `?meeting=` deeplink, a chip) is holding an id this list cannot resolve, and the canvas called
 *  that "Meeting not found" for a meeting the gateway was serving 200.
 *
 *  So BINDING an unknown id ASKS, once. Throttled per id, because every one of those call sites
 *  fires from a render path: the amplification lesson `snapshot()` already carries (2026-09-02, one
 *  idle browser turned into 519 `GET /api/meetings` in three minutes) counts double for something a
 *  re-render can trigger. A row that is PRESENT costs nothing and clears its own throttle, so an id
 *  that goes away and comes back is asked for again rather than remembered as hopeless. */
const REBIND_ASK_MS = 10_000;
const askedFor = new Map<string, number>();
export function ensureMeetingKnown(id: string | null | undefined): void {
  const key = (id ?? "").trim();
  if (!key || typeof window === "undefined") return;
  // Match the way every consumer addresses a meeting: the ROW id, or the native code a deeplink or
  // a chip carries. Either one being present means there is nothing to ask for.
  if (meetings.some((m) => m.id === key || m.native_id === key)) { askedFor.delete(key); return; }
  const now = Date.now();
  const last = askedFor.get(key);
  if (last !== undefined && now - last < REBIND_ASK_MS) return;
  for (const [k, at] of askedFor) if (now - at >= REBIND_ASK_MS) askedFor.delete(k);
  askedFor.set(key, now);
  void snapshot();
}

/** Subscribe a component to the live `meeting.status` stream's CONNECTION state. `false` means the
 *  rows are the last snapshot, not live truth — state-bearing controls (Stop bot …) must degrade
 *  to indeterminate/disabled until it is `true` again (ws.v1 is the authoritative state channel). */
export function useLiveMeetingsConnection(): boolean {
  ensureStarted();
  return useSyncExternalStore(
    (cb) => { subs.add(cb); return () => subs.delete(cb); },
    () => wsConnected,
    () => false,
  );
}

/** Subscribe a component to whether the meetings list has ANSWERED at least once. `false` means "still
 *  resolving"; `true` means the list is authoritative, so an id missing from it does not exist for this
 *  user (deleted, never existed, or owned by someone else and not shared). */
export function useLiveMeetingsLoaded(): boolean {
  ensureStarted();
  return useSyncExternalStore(
    (cb) => { subs.add(cb); return () => subs.delete(cb); },
    () => loaded,
    () => false,
  );
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

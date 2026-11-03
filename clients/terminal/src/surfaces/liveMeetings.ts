"use client";
/** meetings feed — the terminal's REAL meetings list (live AND past), sourced from meeting-api via the
 *  gateway: `GET /api/meetings` → gateway → meeting-api `GET /meetings`. Each row is shaped
 *  {id, platform, native_meeting_id, status, start_time, end_time, data:{recordings:[...]}}, newest-first.
 *  Live meetings carry a `session_uid` so the tab subscribes to the copilot stream; past meetings open a
 *  recorded view whose transcript is fetched on demand from `GET /api/transcripts/{platform}/{native}`. */
import { sharedWithCount } from "./meetingShareApi";
import { useSyncExternalStore } from "react";
import type { MeetingMock, TranscriptLine } from "./meetingModel";
import { onGatewayWSConnected, onMeetingStatus } from "./gatewayWS";
import { readMeetingLanguage } from "./transcriptionLanguage";
import { provideMeetingSource } from "../ui-kit/meetingSource";

/** A row from meeting-api GET /meetings (live AND past). */
export interface MeetingRowDTO {
  id: number | string;
  platform: string;
  native_meeting_id: string | null;   // null on a link-less PLANNED meeting (platform 'unknown')
  status: string;
  shared?: boolean;   // surfaced via a share/membership (not owned by the caller)
  start_time?: string | null;
  end_time?: string | null;
  /** when the row was made — the request, for a bot sent ad hoc. meeting-api orders the list by
   *  COALESCE(data.scheduled_at, start_time, created_at), so a call that never started still has a time. */
  created_at?: string | null;
  constructed_meeting_url?: string | null;
  data?: {
    recordings?: unknown[];
    docs?: { workspace: string; path: string; title?: string; kind?: string }[];
    scheduled_at?: string;
    stop_requested?: boolean;
    // planned-meeting keys (POST /meetings / calendar sync)
    title?: string;
    workspace_id?: string;
    // the reader ids this meeting is shared with — meeting-api ships it to the OWNER only
    transcript_viewers?: unknown[];
    calendar_uid?: string;
    auto_join?: boolean;
    auto_join_error?: string;
    constructed_meeting_url?: string;
    attendees?: { email: string; name?: string; partstat?: string }[];
    // stamped by meeting-api when the owner deletes the meeting's transcript and recordings; the
    // row itself is kept as history. Its shape is api.v1's `ArtifactDeletion`
    // (core/gateway/contracts/api.v1), and `artifactsDeleted` below is its one reader here.
    artifact_deletion?: { state: "pending" | "completed"; [extra: string]: unknown } | null;
    // the language setting the bot runs with, and where it came from; rewritten on every live switch
    transcription_language?: { language?: string | null; allowed_languages?: string[] | null; source?: string } | null;
    // A VEXA CALL (founder O1, 2026-10-18): the session it is held in, why it ended, and what it is
    // called — who or where (`call_named`), renamed by its topic (`call_renamed_from`)
    session_id?: string | null;
    completion_reason?: string | null;
    // caller-set metadata: agent-api stamps `room` on a call started in a room (calls.py `_own_by_room`)
    metadata?: { room?: string; call_named?: string; call_renamed_from?: string; call_kind?: string;
      call_with?: { subject?: string; name?: string }[]; [extra: string]: unknown } | null;
  } | null;
}

/** Has the owner deleted (or is deleting) this meeting's transcript and recordings? The api.v1
 *  `MeetingResponse.data.artifact_deletion` stamp, in either of its states, says so; its tests read
 *  the contract's own goldens, so a reshape of that field fails here rather than silently showing a
 *  deleted meeting as one with a transcript. */
export function artifactsDeleted(d: Pick<MeetingRowDTO, "data">): boolean {
  const stamp = d.data?.artifact_deletion;
  return !!stamp && typeof stamp === "object";
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
  end?: number | null;
  absolute_start_time?: string | null;
  absolute_end_time?: string | null;
  speaker?: string | null;
  text?: string | null;
  /** the segment's id (api.v1) — what a transcript signal annotates */
  segment_id?: string | null;
}

interface GapDTO {
  start?: number | null;
  end?: number | null;
  absolute_start_time?: string | null;
  absolute_end_time?: string | null;
  label?: string | null;
}

interface TranscriptResponseDTO {
  segments?: SegmentDTO[];
  /** Present only because the fetch asks (`include_gaps=true`); a 0.12 reader never sees it. */
  gaps?: GapDTO[];
}

/** The durable half of a meeting's transcript response: the raw recorded segments, mapped for the
 *  transcript pane. There is no second, "processed" body — PRD decision 34 removed the in-product
 *  inference pipeline that produced it, so `data.processed` has no producer and is not read. */
export interface DurableTranscript {
  lines: TranscriptLine[];
  /** Captured audio that was never transcribed, as meeting-api recorded it (prod-cutover R4). */
  gaps?: { startMs?: number; endMs?: number; label: string }[];
}

function transcriptEpochMs(seconds?: number | null, iso?: string | null): number | undefined {
  if (iso && Number.isFinite(Date.parse(iso))) return Date.parse(iso);
  return typeof seconds === "number" && Number.isFinite(seconds) && seconds >= 1e9 ? seconds * 1000 : undefined;
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

/** The name a meeting has when nobody named it: platform · code, or "Untitled meeting" without a link. */
function fallbackTitle(platform: string, _native?: string | null): string {
  // NEVER THE NATIVE ID OR THE PLATFORM KEY (UI decisions 14): a placeholder word that the one name
  // rule (`minutes/callsList` meetingDisplayName) passes over to the chat's name, then the people
  const word: Record<string, string> = { google_meet: "Google Meet call", zoom: "Zoom call", teams: "Teams call", jitsi: "Jitsi call", vexa_call: "Call", voice_note: "Voice note" };
  return word[platform] ?? "Meeting";
}

/** A Vexa call's own fields, read once here (founder O1, 2026-10-18): its session, its room, why it
 *  ended, what it was called before its topic renamed it, and — for a call with one person — both of
 *  them, so each side reads "Call with <the other>". */
export function vexaCallFields(d: MeetingRowDTO): Partial<MeetingMock> {
  if (d.platform !== "vexa_call") return {};
  const meta = d.data?.metadata ?? {};
  const out: Partial<MeetingMock> = {};
  if (typeof d.data?.session_id === "string" && d.data.session_id) out.call_session = d.data.session_id;
  if (typeof meta.room === "string" && meta.room) out.call_room = meta.room;
  if (typeof d.data?.completion_reason === "string" && d.data.completion_reason) out.completion_reason = d.data.completion_reason;
  if (typeof meta.call_renamed_from === "string" && meta.call_renamed_from) out.renamed_from = meta.call_renamed_from;
  if (meta.call_kind === "person" && Array.isArray(meta.call_with)) {
    out.call_with = meta.call_with.map((p) => ({ subject: String(p?.subject ?? ""), name: String(p?.name ?? "") })).filter((p) => p.name || p.subject);
  }
  return out;
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
    title: d.data?.title || (d.platform === "voice_note" ? "Voice note" : fallbackTitle(d.platform, native)),
    title_custom: d.data?.title ?? undefined,
    when: whenLabel(d, live),
    status: live ? "live" : "past",
    live_status: raw,
    shared: !!d.shared,   // owned by someone else, surfaced via a share/membership (data.shared)
    shared_with: d.shared ? undefined : sharedWithCount(d.data),
    scheduled_at: d.data?.scheduled_at ?? undefined,
    workspace_id: d.data?.workspace_id ?? undefined,
    calendar_uid: d.data?.calendar_uid ?? undefined,
    attendees: d.data?.attendees ?? undefined,
    auto_join: d.data?.auto_join,
    auto_join_error: d.data?.auto_join_error ?? undefined,
    meeting_url: d.constructed_meeting_url ?? d.data?.constructed_meeting_url ?? undefined,
    start_time: d.start_time ?? undefined,
    end_time: d.end_time ?? undefined,
    created_at: d.created_at ?? undefined,
    platform: d.platform === "google_meet" ? "Google Meet" : d.platform,
    has_recording: !!(d.data?.recordings?.length),
    docs: d.data?.docs ?? [],
    artifacts_deleted: artifactsDeleted(d),
    transcription_language: readMeetingLanguage(d.data?.transcription_language),
    room: typeof d.data?.metadata?.room === "string" && d.data.metadata.room ? d.data.metadata.room : undefined,
    ...vexaCallFields(d),
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
      `${x.id}|${x.live_status}|${x.has_recording}|${x.artifacts_deleted ? "deleted" : ""}|${x.title_custom ?? ""}|${x.scheduled_at ?? ""}|${x.workspace_id ?? ""}|${x.auto_join ?? ""}|${x.auto_join_error ?? ""}|${x.native_id ?? ""}|${(x.attendees ?? []).map((a) => a.email).join("+")}|${JSON.stringify(x.transcription_language ?? null)}|${x.room ?? ""}`,
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
    const r = await fetch(`/api/transcripts/by-id/${encodeURIComponent(meetingId)}?include_gaps=true`, { cache: "no-store" });
    if (!r.ok) return EMPTY_DURABLE;
    const body = (await r.json()) as TranscriptResponseDTO;
    const list = body.segments || [];
    const lines = list
      .filter((s) => (s.text ?? "").trim())
      .map((s) => ({
        t: formatTranscriptTime(s.start),
        tsMs: transcriptEpochMs(s.start, s.absolute_start_time),
        endMs: transcriptEpochMs(s.end, s.absolute_end_time),
        offsetSeconds: typeof s.start === "number" && s.start < 1e9 ? s.start : undefined,
        speaker: s.speaker || "Speaker", text: s.text ?? "",
        ...(s.segment_id ? { id: s.segment_id } : {}),
      }));
    const gaps = (body.gaps || [])
      .filter((g) => (g.label ?? "").trim())
      .map((g) => ({
        startMs: transcriptEpochMs(g.start, g.absolute_start_time),
        endMs: transcriptEpochMs(g.end, g.absolute_end_time),
        label: g.label as string,
      }));
    return gaps.length ? { lines, gaps } : { lines };
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

/** A RENAME, applied to the store the moment meeting-api has answered 200 — so every view that
 *  reads this store (the Calls list, the rail's meeting rows, the meeting header, the tab strip)
 *  shows the new title at once instead of after the next snapshot. meeting-api stays the record:
 *  this patches the last-known copy of what the annotate route just wrote, and the re-snapshot that
 *  follows replaces it with the stored row. An empty title is the route's "clear", so the row falls
 *  back to its default label exactly as `toMock` would build it. */
export function applyMeetingTitle(id: string, title: string): void {
  const i = meetings.findIndex((m) => m.id === id);
  if (i < 0) return;
  storeRevision += 1;
  const cur = meetings[i];
  const t = title.trim();
  const platformRaw = cur.platform === "Google Meet" ? "google_meet" : cur.platform;
  const next: MeetingMock = { ...cur, title_custom: t || undefined, title: t || fallbackTitle(platformRaw, cur.native_id) };
  meetings = [...meetings.slice(0, i), next, ...meetings.slice(i + 1)];
  subs.forEach((fn) => fn());
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

// The ui-kit's meeting chip reads this list through a slot it names (`ui-kit/meetingSource`): the
// ui-kit sits below this folder and never imports it (Vexa-ai/vexa#1783).
provideMeetingSource({ useMeetings: useLiveMeetings, useLoaded: useLiveMeetingsLoaded, ensureKnown: ensureMeetingKnown });

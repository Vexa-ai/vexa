"use client";
/** meetings feed — the terminal's REAL meetings list (live AND past), sourced from meeting-api via the
 *  gateway: `GET /api/meetings` → gateway → meeting-api `GET /meetings`. Each row is shaped
 *  {id, platform, native_meeting_id, status, start_time, end_time, data:{recordings:[...]}}, newest-first.
 *  Live meetings carry a `session_uid` so the tab subscribes to the copilot stream; past meetings open a
 *  recorded view whose transcript is fetched on demand from `GET /api/transcripts/{platform}/{native}`. */
import { useSyncExternalStore } from "react";
import type { MeetingMock, TranscriptLine } from "./mock";

/** A row from meeting-api GET /meetings (live AND past). */
interface MeetingRowDTO {
  id: number | string;
  platform: string;
  native_meeting_id: string;
  status: string;
  start_time?: string | null;
  end_time?: string | null;
  data?: { recordings?: unknown[] } | null;
}

/** A transcript segment from meeting-api GET /transcripts/{platform}/{native}. */
interface SegmentDTO {
  start?: number | null;
  speaker?: string | null;
  text?: string | null;
}

const LIVE_STATUSES = new Set(["active", "joining", "requested"]);

let meetings: MeetingMock[] = [];
const subs = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function whenLabel(d: MeetingRowDTO, live: boolean): string {
  if (live) return "Now · live";
  if (!d.start_time) return "Recorded";
  try { return new Date(d.start_time).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return "Recorded"; }
}

function toMock(d: MeetingRowDTO): MeetingMock {
  const live = LIVE_STATUSES.has(d.status);
  const native = d.native_meeting_id;
  return {
    id: native,
    native_id: native,
    session_uid: live ? native : undefined,  // only live meetings subscribe to the copilot stream
    title: `${d.platform} · ${native}`,
    when: whenLabel(d, live),
    status: live ? "live" : "past",
    platform: d.platform === "google_meet" ? "Google Meet" : d.platform,
    has_recording: !!(d.data?.recordings?.length),
    participants: [],
    mentioned: [],
    actions: [],
    transcript: [],
    insights: [],
  };
}

async function poll() {
  try {
    const r = await fetch("/api/meetings", { cache: "no-store" });
    const { meetings: list } = (await r.json()) as { meetings: MeetingRowDTO[] };
    const next = (list || []).map(toMock);
    const key = (m: MeetingMock[]) => m.map((x) => `${x.id}|${x.status}|${x.has_recording}`).join(",");
    if (key(next) !== key(meetings)) {
      meetings = next;
      subs.forEach((f) => f());
    }
  } catch {
    /* offline — keep last known */
  }
}

function ensurePolling() {
  if (timer || typeof window === "undefined") return;
  poll();
  timer = setInterval(poll, 4000);
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

/** Subscribe a component to the meetings feed (live + past). */
export function useLiveMeetings(): MeetingMock[] {
  ensurePolling();
  return useSyncExternalStore(
    (cb) => { subs.add(cb); return () => subs.delete(cb); },
    () => meetings,
    () => meetings,
  );
}

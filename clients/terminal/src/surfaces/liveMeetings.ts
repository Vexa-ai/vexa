"use client";
/** meetings feed — the terminal's REAL meetings list (live AND past), from `agent-api GET /api/meetings`
 *  (which proxies meeting-api + merges the live copilot registry). Live meetings carry a `session_uid` so
 *  the tab subscribes to the copilot stream; past meetings open a recorded view (transcript + recording). */
import { useSyncExternalStore } from "react";
import type { MeetingMock } from "./mock";

interface MeetingRowDTO {
  native_id: string; platform: string; title: string; status: string;
  start?: string | null; end?: string | null; has_recording?: boolean; unit_id?: string | null;
}

let meetings: MeetingMock[] = [];
const subs = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function whenLabel(d: MeetingRowDTO, live: boolean): string {
  if (live) return "Now · live";
  if (!d.start) return "Recorded";
  try { return new Date(d.start).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return "Recorded"; }
}

function toMock(d: MeetingRowDTO): MeetingMock {
  const live = d.status === "live";
  return {
    id: d.native_id,
    native_id: d.native_id,
    session_uid: live ? d.native_id : undefined,  // only live meetings subscribe to the copilot stream
    title: d.title || `${d.platform} · ${d.native_id}`,
    when: whenLabel(d, live),
    status: live ? "live" : "past",
    platform: d.platform === "google_meet" ? "Google Meet" : d.platform,
    has_recording: !!d.has_recording,
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

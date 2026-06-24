"use client";
/** liveMeetings — the terminal's feed of REAL active meeting copilots (agent-api's live registry).
 *  Polls `/api/meetings/live` and maps each to a MeetingMock so the existing meeting tab + transcript
 *  pane render it via `useMeetingLive(meeting_id, session_uid)` — a real Vexa Cloud bot's transcript +
 *  the copilot's cards stream in over `/api/meeting/stream`, no mock involved. */
import { useSyncExternalStore } from "react";
import type { MeetingMock } from "./mock";

interface LiveMeetingDTO { meeting_id: string; session_uid: string; platform: string; title: string; native_id?: string; status?: string }

let meetings: MeetingMock[] = [];
const subs = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function toMock(d: LiveMeetingDTO): MeetingMock {
  const stopped = d.status === "stopped";
  return {
    id: d.meeting_id,
    session_uid: d.session_uid,
    native_id: d.native_id ?? d.meeting_id,
    title: d.title || `${d.platform} · ${d.meeting_id}`,
    when: stopped ? "Stopped" : "Now · live",
    status: stopped ? "past" : "live",
    platform: d.platform === "google_meet" ? "Google Meet" : d.platform,
    participants: [],
    mentioned: [],
    actions: [],
    transcript: [],
    insights: [],
  };
}

async function poll() {
  try {
    const r = await fetch("/api/meetings/live", { cache: "no-store" });
    const { meetings: list } = (await r.json()) as { meetings: LiveMeetingDTO[] };
    const next = (list || []).map(toMock);
    // only re-render when the set of live meetings actually changes (id+session_uid)
    const key = (m: MeetingMock[]) => m.map((x) => `${x.id}|${x.session_uid}|${x.status}`).join(",");
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

/** Last-known live meetings (sync) — lets non-hook lookups resolve a real meeting by id. */
export function getLiveMeeting(id: string): MeetingMock | undefined {
  return meetings.find((m) => m.id === id);
}

/** All last-known real live meetings (sync) — used by the auto-open command. */
export function liveMeetingsNow(): MeetingMock[] {
  return meetings;
}

/** Subscribe a component to the live-meetings feed. */
export function useLiveMeetings(): MeetingMock[] {
  ensurePolling();
  return useSyncExternalStore(
    (cb) => { subs.add(cb); return () => subs.delete(cb); },
    () => meetings,
    () => meetings,
  );
}

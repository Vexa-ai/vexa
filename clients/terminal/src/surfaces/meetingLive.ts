"use client";
/** meetingLive — one shared SSE subscription per live meeting, read by the transcript pane + the
 *  meeting tab. The backend `/api/meeting/stream` merges the transcript Stream + the copilot's output
 *  Stream (cards · the agent working) into one feed; we accumulate it into an observable store so
 *  several components render the same live state without each opening its own connection. */
import { useSyncExternalStore } from "react";

export interface LiveSegment { speaker: string; text: string; t?: number; id?: string; completed?: boolean }
export interface LiveCard { kind: string; title: string; body?: string }
export interface LiveState {
  transcript: LiveSegment[];
  cards: LiveCard[];
  note: string;            // the agent's latest message-delta (what the copilot is thinking)
  ended: boolean;
  connected: boolean;
}

interface Entry { state: LiveState; subs: Set<() => void>; es?: EventSource }
const stores = new Map<string, Entry>();
const EMPTY: LiveState = { transcript: [], cards: [], note: "", ended: false, connected: false };

function ensure(meetingId: string, sessionUid: string): Entry {
  const key = `${meetingId}|${sessionUid}`;
  let e = stores.get(key);
  if (e) return e;
  e = { state: { ...EMPTY }, subs: new Set() };
  stores.set(key, e);
  if (typeof window === "undefined" || !sessionUid) return e; // SSR / non-live — no EventSource

  const emit = () => { e!.state = { ...e!.state }; e!.subs.forEach((f) => f()); };
  const es = new EventSource(`/api/meeting/stream?meeting_id=${encodeURIComponent(meetingId)}&session_uid=${encodeURIComponent(sessionUid)}`);
  e.es = es;
  es.onopen = () => { e!.state.connected = true; emit(); };
  es.onmessage = (m) => {
    let ev: { type?: string; speaker?: string; text?: string; t?: number; id?: string; completed?: boolean; card?: LiveCard };
    try { ev = JSON.parse(m.data); } catch { return; }
    const s = e!.state;
    if (ev.type === "transcript") {
      // pending (completed:false) segments arrive repeatedly as ASR refines — upsert on segment id so
      // the line updates in place (and finalizes when completed:true), rather than piling up duplicates.
      const seg: LiveSegment = { speaker: ev.speaker ?? "?", text: ev.text ?? "", t: ev.t, id: ev.id, completed: ev.completed !== false };
      const i = ev.id ? s.transcript.findIndex((x) => x.id === ev.id) : -1;
      if (i >= 0) s.transcript[i] = seg; else s.transcript.push(seg);
    }
    else if (ev.type === "card" && ev.card) s.cards.push(ev.card);
    else if (ev.type === "message-delta" && ev.text) s.note = ev.text;
    else if (ev.type === "meeting-end") { s.ended = true; es.close(); }
    else return; // ping / tool-call / etc. — ignore for now
    emit();
  };
  es.onerror = () => { e!.state.connected = false; emit(); };
  return e;
}

/** Subscribe a component to a live meeting's merged stream. */
export function useMeetingLive(meetingId: string, sessionUid: string): LiveState {
  const e = ensure(meetingId, sessionUid);
  return useSyncExternalStore(
    (cb) => { e.subs.add(cb); return () => e.subs.delete(cb); },
    () => e.state,
    () => e.state,
  );
}

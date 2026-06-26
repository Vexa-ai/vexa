"use client";
/** meetingLive — one shared SSE subscription per live meeting, read by the transcript pane + the
 *  meeting tab. The backend `/api/meeting/stream` merges the transcript Stream + the copilot's output
 *  Stream (cards · the agent working) into one feed; we accumulate it into an observable store so
 *  several components render the same live state without each opening its own connection.
 *
 *  Lifecycle: the connection is owned by an EFFECT (not opened during render) and refcounted across
 *  subscribers — so it opens exactly when a real `session_uid` is known, survives a load-order race
 *  (the meetings list resolves async; the first render often has no session_uid yet), reconnects on a
 *  transient error, and closes when the last subscriber unmounts. */
import { useEffect } from "react";
import { useSyncExternalStore } from "react";

export interface LiveSegment { speaker: string; text: string; t?: number; id?: string; completed?: boolean }
export interface LiveCard { kind: string; title: string; body?: string }
export interface LiveNote { id: string; speaker?: string; chapter?: string; text: string; t?: number; pass?: number; frozen?: boolean }
export interface LiveModelError { stage?: string; model?: string; message: string; t?: number }
export interface LiveState {
  transcript: LiveSegment[];
  notes: LiveNote[];
  cards: LiveCard[];
  errors: LiveModelError[];
  note: string;            // the agent's latest message-delta (what the copilot is thinking)
  ended: boolean;
  connected: boolean;
}

interface Entry { state: LiveState; subs: Set<() => void>; es?: EventSource; refs: number; retry?: number }
const stores = new Map<string, Entry>();
const EMPTY: LiveState = { transcript: [], notes: [], cards: [], errors: [], note: "", ended: false, connected: false };
const RECONNECT_MS = 2500;

function ensure(key: string): Entry {
  let e = stores.get(key);
  if (!e) { e = { state: { ...EMPTY }, subs: new Set(), refs: 0 }; stores.set(key, e); }
  return e;
}

/** Open the SSE for an entry if it isn't already connected. Idempotent — safe to call repeatedly. */
function connect(e: Entry, meetingId: string, sessionUid: string): void {
  if (e.es || typeof window === "undefined" || !sessionUid || e.state.ended) return;

  // Emit a NEW state object AND fresh array refs, so downstream `useMemo`s keyed on `transcript`/`cards`
  // recompute (mutating the arrays in place leaves their identity stable and the memo would go stale).
  const emit = () => {
    const s = e.state;
    e.state = { ...s, transcript: [...s.transcript], notes: [...s.notes], cards: [...s.cards], errors: [...s.errors] };
    e.subs.forEach((f) => f());
  };

  const es = new EventSource(`/api/meeting/stream?meeting_id=${encodeURIComponent(meetingId)}&session_uid=${encodeURIComponent(sessionUid)}`);
  e.es = es;
  es.onopen = () => { e.state.connected = true; emit(); };
  es.onmessage = (m) => {
    let ev: { type?: string; speaker?: string; text?: string; t?: number; id?: string; completed?: boolean; card?: LiveCard; note?: LiveNote; error?: LiveModelError | string };
    try { ev = JSON.parse(m.data); } catch { return; }
    const s = e.state;
    if (ev.type === "transcript") {
      // pending (completed:false) segments arrive repeatedly as ASR refines — upsert on segment id so
      // the line updates in place (and finalizes when completed:true), rather than piling up duplicates.
      const seg: LiveSegment = { speaker: ev.speaker ?? "?", text: ev.text ?? "", t: ev.t, id: ev.id, completed: ev.completed !== false };
      const i = ev.id ? s.transcript.findIndex((x) => x.id === ev.id) : -1;
      if (i >= 0) s.transcript[i] = seg; else s.transcript.push(seg);
    }
    else if (ev.type === "card" && ev.card) s.cards.push(ev.card);
    else if (ev.type === "model-error") {
      const raw = ev.error;
      const err: LiveModelError = typeof raw === "string"
        ? { message: raw }
        : { stage: raw?.stage, model: raw?.model, message: raw?.message || "Model inference failed", t: ev.t };
      s.errors.push(err);
      if (s.errors.length > 20) s.errors.splice(0, s.errors.length - 20);
      s.cards.push({
        kind: "warning",
        title: "Model inference error",
        body: [err.model, err.stage, err.message].filter(Boolean).join(" · "),
      });
    }
    else if (ev.type === "note" && ev.note?.id && ev.note.text) {
      const next: LiveNote = { id: ev.note.id, speaker: ev.note.speaker, chapter: ev.note.chapter, text: ev.note.text, t: ev.note.t, pass: ev.note.pass, frozen: ev.note.frozen };
      const i = s.notes.findIndex((x) => x.id === next.id);
      if (i >= 0) s.notes[i] = next; else s.notes.push(next);
    }
    else if (ev.type === "message-delta" && ev.text) s.note = ev.text;
    else if (ev.type === "meeting-end") { s.ended = true; es.close(); e.es = undefined; }
    else return; // ping / tool-call / etc. — ignore for now
    emit();
  };
  es.onerror = () => {
    e.state.connected = false;
    es.close();
    e.es = undefined;
    emit();
    // Transient drop (agent-api restart / ECONNRESET on the proxy): reconnect while anyone still cares.
    if (!e.state.ended && e.refs > 0 && e.retry == null) {
      e.retry = window.setTimeout(() => { e.retry = undefined; connect(e, meetingId, sessionUid); }, RECONNECT_MS);
    }
  };
}

function disconnect(e: Entry): void {
  if (e.retry != null) { window.clearTimeout(e.retry); e.retry = undefined; }
  e.es?.close();
  e.es = undefined;
  if (e.state.connected) { e.state = { ...e.state, connected: false }; e.subs.forEach((f) => f()); }
}

/** Subscribe a component to a live meeting's merged stream. The connection is owned by an effect and
 *  refcounted: it opens once `sessionUid` is real, reconnects on error, and closes on last unmount. */
export function useMeetingLive(meetingId: string, sessionUid: string): LiveState {
  const key = `${meetingId}|${sessionUid}`;
  const e = ensure(key);

  useEffect(() => {
    if (typeof window === "undefined" || !sessionUid) return;
    e.refs += 1;
    connect(e, meetingId, sessionUid);
    return () => {
      e.refs -= 1;
      if (e.refs <= 0) disconnect(e);
    };
  }, [e, meetingId, sessionUid]);

  return useSyncExternalStore(
    (cb) => { e.subs.add(cb); return () => e.subs.delete(cb); },
    () => e.state,
    () => e.state,
  );
}

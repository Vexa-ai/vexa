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

export interface LiveSegment { speaker: string; text: string; t?: number; tsMs?: number; id?: string; completed?: boolean }
export interface LiveCard { kind: string; title: string; body?: string }
export interface LiveNote { id: string; speaker?: string; chapter?: string; text: string; t?: number; tsMs?: number; pass?: number; frozen?: boolean }
export interface LiveModelError { stage?: string; model?: string; message: string; t?: number }
export interface LiveStreamIssue { kind: "stream" | "model" | "parse"; message: string; status?: number; at: number; model?: string; stage?: string }
export interface LiveState {
  transcript: LiveSegment[];
  notes: LiveNote[];
  cards: LiveCard[];
  errors: LiveModelError[];
  issues: LiveStreamIssue[];
  note: string;            // the agent's latest message-delta (what the copilot is thinking)
  ended: boolean;
  connected: boolean;
  reconnects: number;
  lastEventAt?: number;
  lastTranscriptAt?: number;
}

interface Entry { state: LiveState; subs: Set<() => void>; es?: EventSource; refs: number; retry?: number; watchdog?: number; startEpochMs?: number; lastEventId?: string }
const stores = new Map<string, Entry>();
const EMPTY: LiveState = { transcript: [], notes: [], cards: [], errors: [], issues: [], note: "", ended: false, connected: false, reconnects: 0 };
const RECONNECT_MS = 2500;
// Active staleness watchdog. An EventSource can go SILENTLY half-open — the socket stays "open" but
// no bytes flow, so `onerror` never fires and the existing reconnect path never triggers. We poll
// `lastEventAt` (ANY event, including the server's idle pings ~15s — see agent-api stream) and, if no
// event has arrived for longer than this, force a reconnect ourselves. Must exceed the ping interval
// so a healthy idle feed (pings only) is never mistaken for dead.
const WATCHDOG_MS = 20000;
const WATCHDOG_TICK_MS = 5000;

/** Pure predicate (testable): given the last event time and now, has the feed gone silent past the
 *  threshold and so should be force-reconnected? */
export function shouldForceReconnect(lastEventAt: number | undefined, now: number, watchdogMs = WATCHDOG_MS): boolean {
  if (lastEventAt == null) return false; // never opened / no event yet — connect path owns that
  return now - lastEventAt > watchdogMs;
}

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
    e.state = { ...s, transcript: [...s.transcript], notes: [...s.notes], cards: [...s.cards], errors: [...s.errors], issues: [...s.issues] };
    e.subs.forEach((f) => f());
  };
  const addIssue = (issue: Omit<LiveStreamIssue, "at">) => {
    const s = e.state;
    s.issues.push({ ...issue, at: Date.now() });
    if (s.issues.length > 20) s.issues.splice(0, s.issues.length - 20);
  };
  // The SSE only carries meeting-RELATIVE seconds (`t`). To give each line an ABSOLUTE wall-clock
  // (epoch ms), anchor the meeting's start: the wall-clock when t=0 is `now - t*1000`. We lock the
  // anchor on the first line we see (seed replay arrives in one burst at ~one wall-clock, so this is
  // accurate to that burst), and only lower it if a later line reports a smaller `t` — keeping the
  // anchor pinned to the truest meeting start. Then tsMs(line) = startEpochMs + t*1000.
  const absMs = (t: number | undefined): number | undefined => {
    if (typeof t !== "number" || !Number.isFinite(t)) return undefined;
    const candidate = Date.now() - t * 1000;
    if (e.startEpochMs == null || candidate < e.startEpochMs) e.startEpochMs = candidate;
    return e.startEpochMs + t * 1000;
  };

  // Force a reconnect through the SAME path `onerror` takes — used by both the error handler and the
  // staleness watchdog (a half-open socket that never fires onerror).
  const forceReconnect = (reason: string) => {
    if (e.state.ended) return;
    clearWatchdog(e);
    e.state.connected = false;
    e.state.reconnects += 1;
    addIssue({ kind: "stream", message: reason });
    es.close();
    e.es = undefined;
    emit();
    if (e.refs > 0 && e.retry == null) {
      e.retry = window.setTimeout(() => { e.retry = undefined; connect(e, meetingId, sessionUid); }, RECONNECT_MS);
    }
  };

  // One polling watchdog per entry: if no event (incl. pings) has arrived within WATCHDOG_MS, the
  // socket is silently dead → force-reconnect. `lastEventAt` is refreshed on every received event, so
  // this self-resets without restarting the timer.
  const armWatchdog = () => {
    clearWatchdog(e);
    e.watchdog = window.setInterval(() => {
      if (e.es !== es || e.state.ended) { clearWatchdog(e); return; }
      if (shouldForceReconnect(e.state.lastEventAt, Date.now())) {
        forceReconnect("Meeting stream went silent (no events); reconnecting");
      }
    }, WATCHDOG_TICK_MS);
  };

  // Resume from the last segment we saw so a RECONNECT is gapless (the proxy turns `lid` into the
  // Last-Event-ID header agent-api resumes from). A manual forceReconnect opens a FRESH EventSource,
  // which would otherwise lose the browser's native Last-Event-ID — so we carry it ourselves.
  const resume = e.lastEventId ? `&lid=${encodeURIComponent(e.lastEventId)}` : "";
  const es = new EventSource(`/api/meeting/stream?meeting_id=${encodeURIComponent(meetingId)}&session_uid=${encodeURIComponent(sessionUid)}${resume}`);
  e.es = es;
  es.onopen = () => { e.state.connected = true; e.state.lastEventAt = Date.now(); armWatchdog(); emit(); };
  es.onmessage = (m) => {
    let ev: { type?: string; speaker?: string; text?: string; t?: number; tsMs?: number; id?: string; completed?: boolean; card?: LiveCard; note?: LiveNote; error?: LiveModelError | string; message?: string; status?: number };
    try { ev = JSON.parse(m.data); } catch {
      addIssue({ kind: "parse", message: "Could not parse meeting stream event" });
      emit();
      return;
    }
    const s = e.state;
    s.lastEventAt = Date.now();
    if (m.lastEventId) e.lastEventId = m.lastEventId;   // remember the cursor for a gapless reconnect
    if (ev.type === "transcript") {
      // pending (completed:false) segments arrive repeatedly as ASR refines — upsert on segment id so
      // the line updates in place (and finalizes when completed:true), rather than piling up duplicates.
      // Prefer the bot's canonical ABSOLUTE wall-clock (`ev.tsMs`, epoch ms) carried through the
      // agent-api seam; fall back to the connect-time anchor only when the event lacks it (old/seed data).
      const tsMs = (typeof ev.tsMs === "number" && Number.isFinite(ev.tsMs)) ? ev.tsMs : absMs(ev.t);
      const seg: LiveSegment = { speaker: ev.speaker ?? "?", text: ev.text ?? "", t: ev.t, tsMs, id: ev.id, completed: ev.completed !== false };
      const i = ev.id ? s.transcript.findIndex((x) => x.id === ev.id) : -1;
      if (i >= 0) s.transcript[i] = seg; else s.transcript.push(seg);
      s.lastTranscriptAt = s.lastEventAt;
    }
    else if (ev.type === "card" && ev.card) s.cards.push(ev.card);
    else if (ev.type === "model-error") {
      const raw = ev.error;
      const err: LiveModelError = typeof raw === "string"
        ? { message: raw }
        : { stage: raw?.stage, model: raw?.model, message: raw?.message || "Model inference failed", t: ev.t };
      s.errors.push(err);
      if (s.errors.length > 20) s.errors.splice(0, s.errors.length - 20);
      addIssue({ kind: "model", model: err.model, stage: err.stage, message: err.message });
      s.cards.push({
        kind: "warning",
        title: "Model inference error",
        body: [err.model, err.stage, err.message].filter(Boolean).join(" · "),
      });
    }
    else if (ev.type === "note" && ev.note?.id && ev.note.text) {
      const next: LiveNote = { id: ev.note.id, speaker: ev.note.speaker, chapter: ev.note.chapter, text: ev.note.text, t: ev.note.t, tsMs: absMs(ev.note.t), pass: ev.note.pass, frozen: ev.note.frozen };
      const i = s.notes.findIndex((x) => x.id === next.id);
      if (i >= 0) s.notes[i] = next; else s.notes.push(next);
    }
    else if (ev.type === "message-delta" && ev.text) s.note = ev.text;
    else if (ev.type === "stream-error") addIssue({ kind: "stream", message: ev.message || "Meeting stream error", status: ev.status });
    else if (ev.type === "meeting-end") { s.ended = true; clearWatchdog(e); es.close(); e.es = undefined; }
    else return; // ping / tool-call / etc. — ignore for now
    emit();
  };
  es.onerror = () => {
    // Transient drop (agent-api restart / ECONNRESET on the proxy): reconnect while anyone still cares.
    forceReconnect("Meeting stream disconnected; reconnecting");
  };
}

function clearWatchdog(e: Entry): void {
  if (e.watchdog != null) { window.clearInterval(e.watchdog); e.watchdog = undefined; }
}

function disconnect(e: Entry): void {
  if (e.retry != null) { window.clearTimeout(e.retry); e.retry = undefined; }
  clearWatchdog(e);
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

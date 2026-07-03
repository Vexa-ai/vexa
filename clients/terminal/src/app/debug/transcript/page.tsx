"use client";
/** ISOLATED transcript-pipeline probe. Bypasses the authored canvas view, the mock source, dockview, and
 *  the agent copilot — it subscribes to ONLY the two stores that carry live transcript:
 *    useLiveMeetings()  → resolves the meeting row + its session_uid (the bind)
 *    useMeetingLive()   → the merged SSE feed (transcript + cards + note + connection state)
 *  so we can see, for any live meeting, whether segments are arriving and re-rendering in real time —
 *  independent of everything downstream.  Open /debug/transcript?id=<native_meeting_id>. */
import { useEffect, useRef, useState } from "react";
import { useLiveMeetings } from "../../../surfaces/liveMeetings";
import { useMeetingLive } from "../../../surfaces/meetingLive";

function useQueryId(): string {
  const [id, setId] = useState("");
  useEffect(() => { setId(new URLSearchParams(window.location.search).get("id") ?? ""); }, []);
  return id;
}

function ageLabel(at?: number): string {
  if (!at) return "never";
  const s = Math.max(0, Math.round((Date.now() - at) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s ago`;
}

function hms(at: number): string {
  const d = new Date(at);
  return `${d.toLocaleTimeString()}.${String(at % 1000).padStart(3, "0")}`;
}

/** RAW WIRE — the UNCLEANED truth: a dedicated EventSource that appends EVERY transcript event
 *  exactly as it arrives off the SSE (no upsert-by-id, no note shaping, no copilot).
 *  This is what the bot actually emits — every pending refinement, every re-word — so you can watch
 *  the raw ASR behaviour in real time. Capped to the last 600 events. */
interface RawEvent { at: number; speaker?: string; text?: string; completed?: boolean; id?: string; tsMs?: number }
function useRawWire(meetingId: string, sessionUid: string): RawEvent[] {
  const [events, setEvents] = useState<RawEvent[]>([]);
  useEffect(() => {
    if (typeof window === "undefined" || !sessionUid || !meetingId) return;
    setEvents([]);
    const es = new EventSource(`/api/meeting/stream?meeting_id=${encodeURIComponent(meetingId)}&session_uid=${encodeURIComponent(sessionUid)}`);
    es.onmessage = (m) => {
      let ev: { type?: string; speaker?: string; text?: string; completed?: boolean; id?: string; tsMs?: number };
      try { ev = JSON.parse(m.data); } catch { return; }
      if (ev.type !== "transcript") return;
      setEvents((prev) => {
        const next = [...prev, { at: Date.now(), speaker: ev.speaker, text: ev.text, completed: ev.completed, id: ev.id, tsMs: ev.tsMs }];
        return next.length > 600 ? next.slice(-600) : next;
      });
    };
    return () => es.close();
  }, [meetingId, sessionUid]);
  return events;
}

export default function TranscriptDebug() {
  const requestedId = useQueryId();
  const meetings = useLiveMeetings();
  const row = meetings.find((m) => m.id === requestedId || m.native_id === requestedId);
  const sessionUid = row?.session_uid ?? requestedId;
  const live = useMeetingLive(requestedId || "—", sessionUid);
  const raw = useRawWire(requestedId || "", sessionUid);

  // Render-rate + last-update probe: count renders and stamp when the segment count last changed.
  const renders = useRef(0);
  renders.current += 1;
  const lastCount = useRef(-1);
  const lastChangeAt = useRef<number | null>(null);
  if (live.transcript.length !== lastCount.current) {
    lastCount.current = live.transcript.length;
    lastChangeAt.current = typeof performance !== "undefined" ? Math.round(performance.now()) : null;
  }

  // Everything on this page is live/client-only; render a stable shell on the server to avoid a
  // hydration mismatch on the render counter and connection state.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const mono: React.CSSProperties = { fontFamily: "monospace", fontSize: 12 };
  if (!mounted) {
    return <div style={{ padding: 20, ...mono, color: "#888", background: "var(--bg, #111)", minHeight: "100vh" }} suppressHydrationWarning>loading probe…</div>;
  }
  return (
    <div style={{ padding: 20, color: "var(--t1, #ddd)", background: "var(--bg, #111)", minHeight: "100vh", ...mono }}>
      <h2 style={{ fontSize: 14, marginBottom: 12 }}>Transcript pipeline probe</h2>
      <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", rowGap: 4, marginBottom: 16 }}>
        <span>requested id</span><span>{requestedId || "(none — add ?id=…)"}</span>
        <span>meetings loaded</span><span>{meetings.length}</span>
        <span>row resolved</span><span>{row ? `yes — status=${row.live_status ?? row.status}` : "NO (not in list yet)"}</span>
        <span>session_uid</span><span>{sessionUid || "(empty → no SSE will open)"}</span>
        <span>connected</span><span style={{ color: live.connected ? "#5d5" : "#d55" }}>{String(live.connected)}</span>
        <span>ended</span><span>{String(live.ended)}</span>
        <span>reconnects</span><span>{live.reconnects}</span>
        <span>last event</span><span>{ageLabel(live.lastEventAt)}</span>
        <span>last transcript</span><span>{ageLabel(live.lastTranscriptAt)}</span>
        <span>segments</span><span>{live.transcript.length}</span>
        <span>processed notes</span><span>{live.notes.length}</span>
        <span>cards</span><span>{live.cards.length}</span>
        <span>issues</span><span style={{ color: live.issues.length ? "#d77" : "#888" }}>{live.issues.length}</span>
        <span>react renders</span><span>{renders.current}</span>
        <span>last seg change @</span><span>{lastChangeAt.current == null ? "never" : `${lastChangeAt.current}ms`}</span>
      </div>
      {live.issues.length > 0 && (
        <div style={{ borderTop: "1px solid #444", paddingTop: 10, marginBottom: 14 }}>
          <div style={{ color: "#d77", marginBottom: 6 }}>recent issues</div>
          {live.issues.slice(-8).map((issue, i) => (
            <div key={`${issue.at}-${i}`} style={{ marginBottom: 4 }}>
              <span style={{ color: "#d77" }}>{issue.kind}</span>
              <span style={{ color: "#777" }}> {new Date(issue.at).toLocaleTimeString()} </span>
              <span>{issue.message}{issue.status ? ` (${issue.status})` : ""}</span>
            </div>
          ))}
        </div>
      )}
      {/* RAW WIRE — every event off the SSE, append-only · NO upsert / clustering / copilot */}
      <div style={{ borderTop: "1px solid #444", paddingTop: 10, marginBottom: 14 }}>
        <div style={{ color: "#9c9", marginBottom: 6 }}>RAW WIRE — every event, uncleaned ({raw.length}) · ~ = pending · F = final</div>
        {raw.length === 0 && <div style={{ color: "#888" }}>no raw events yet…</div>}
        {raw.slice(-150).map((ev, i) => (
          <div key={i} style={{ marginBottom: 2, opacity: ev.completed === false ? 0.55 : 1, whiteSpace: "pre-wrap" }}>
            <span style={{ color: "#666" }}>{hms(ev.at)} </span>
            <span style={{ color: ev.completed === false ? "#ca6" : "#5d5" }}>{ev.completed === false ? "~" : "F"} </span>
            <span style={{ color: "#7af" }}>{ev.speaker} </span>
            <span style={{ color: "#666" }}>[{ev.id}] </span>
            <span>{ev.text}</span>
          </div>
        ))}
      </div>
      {/* current state per segment_id (upserted — latest text per id, still pre-clustering) */}
      <div style={{ borderTop: "1px solid #444", paddingTop: 10 }}>
        <div style={{ color: "#9c9", marginBottom: 6 }}>RAW SEGMENTS — current per id ({live.transcript.length})</div>
        {live.transcript.length === 0 && <div style={{ color: "#888" }}>no segments yet…</div>}
        {live.transcript.slice(-40).map((s, i) => (
          <div key={s.id ?? i} style={{ marginBottom: 6, opacity: s.completed === false ? 0.6 : 1 }}>
            <span style={{ color: "#7af" }}>{s.speaker}</span>
            <span style={{ color: "#555" }}>{s.completed === false ? " (pending)" : ""}</span>
            <div>{s.text}</div>
          </div>
        ))}
      </div>
      {live.note && <div style={{ marginTop: 14, color: "#caa" }}>note: {live.note}</div>}
    </div>
  );
}

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

export default function TranscriptDebug() {
  const requestedId = useQueryId();
  const meetings = useLiveMeetings();
  const row = meetings.find((m) => m.id === requestedId || m.native_id === requestedId);
  const sessionUid = row?.session_uid ?? "";
  const live = useMeetingLive(requestedId || "—", sessionUid);

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
        <span>segments</span><span>{live.transcript.length}</span>
        <span>cards</span><span>{live.cards.length}</span>
        <span>react renders</span><span>{renders.current}</span>
        <span>last seg change @</span><span>{lastChangeAt.current == null ? "never" : `${lastChangeAt.current}ms`}</span>
      </div>
      <div style={{ borderTop: "1px solid #444", paddingTop: 10 }}>
        {live.transcript.length === 0 && <div style={{ color: "#888" }}>no segments yet…</div>}
        {live.transcript.slice(-30).map((s, i) => (
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

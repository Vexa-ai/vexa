"use client";
import { useCallback, useEffect, useState } from "react";
import { CanvasActionsProvider } from "./actions";
import { EvalPanel } from "./EvalPanel";
import { CanvasRuntime } from "./runtime";
import { MeetingScopeProvider, MeetingSourceProvider, useMeeting } from "./useMeeting";

const SUBJECT = "u_live";
const VIEW_PATH = "views/meeting.tsx";
export const MEETING_CANVAS_CONTENT_INSET = 18;
const FALLBACK_SOURCE = `
export default function Component() {
  return React.createElement(ui.Empty, { title: "No canvas view", body: "Create views/meeting.tsx in the workspace." });
}
`;

async function readCanvasSource(): Promise<string> {
  try {
    const r = await fetch(`/api/workspace/file?subject=${SUBJECT}&path=${encodeURIComponent(VIEW_PATH)}`, { cache: "no-store" });
    if (!r.ok) return FALLBACK_SOURCE;
    const body = await r.json() as { content?: string };
    return body.content?.trim() ? body.content : FALLBACK_SOURCE;
  } catch {
    return FALLBACK_SOURCE;
  }
}

function CaptionStrip() {
  const meeting = useMeeting();
  const caption = meeting.transcript.liveCaption ?? meeting.transcript.segments[meeting.transcript.segments.length - 1]?.text ?? "Waiting for live meeting feed";
  return (
    <div style={{ minHeight: 30, flex: "none", borderTop: "1px solid var(--line)", background: "var(--sidebar)", color: "var(--t2)", fontSize: 12.5, display: "flex", alignItems: "center", gap: 8, padding: `0 ${MEETING_CANVAS_CONTENT_INSET}px` }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: meeting.transcript.liveCaption ? "var(--live)" : "var(--t3)", flex: "none" }} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{caption}</span>
    </div>
  );
}

export function MeetingCanvasView({ meetingId }: { meetingId?: string }) {
  const [source, setSource] = useState(FALLBACK_SOURCE);
  const [stamp, setStamp] = useState("");
  const load = useCallback(() => {
    void readCanvasSource().then((next) => {
      setSource(next);
      setStamp(new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    });
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 4000);
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [load]);

  return (
    <MeetingScopeProvider meetingId={meetingId}>
      <MeetingSourceProvider meetingId={meetingId}>
        <CanvasActionsProvider>
          <div style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, background: "var(--bg)" }}>
            <header style={{ height: 38, flex: "none", borderBottom: "1px solid var(--line)", background: "var(--sidebar)", display: "flex", alignItems: "center", gap: 10, padding: `0 ${MEETING_CANVAS_CONTENT_INSET}px` }}>
              <div style={{ color: "var(--t1)", fontSize: 13, fontWeight: 650 }}>Meeting Canvas</div>
              <div style={{ color: "var(--t3)", fontSize: 11.5, fontFamily: "var(--mono)" }}>{VIEW_PATH}</div>
              <div style={{ flex: 1 }} />
              {stamp && <div style={{ color: "var(--t3)", fontSize: 11.5 }}>reloaded {stamp}</div>}
            </header>
            <EvalPanel />
            <main style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              <div style={{ padding: MEETING_CANVAS_CONTENT_INSET }}>
                <CanvasRuntime source={source} />
              </div>
            </main>
            <CaptionStrip />
          </div>
        </CanvasActionsProvider>
      </MeetingSourceProvider>
    </MeetingScopeProvider>
  );
}

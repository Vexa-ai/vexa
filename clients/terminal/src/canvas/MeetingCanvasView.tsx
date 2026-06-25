"use client";
import { CanvasActionsProvider } from "./actions";
import { useCanvasSource } from "./canvasSource";
import { EvalPanel } from "./EvalPanel";
import { CanvasRuntime } from "./runtime";
import { MeetingScopeProvider, MeetingSourceProvider } from "./useMeeting";

const VIEW_PATH = "views/meeting.tsx";
export const MEETING_CANVAS_CONTENT_INSET = 18;

export function MeetingCanvasView({ meetingId }: { meetingId?: string }) {
  const { source, stamp } = useCanvasSource();

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
          </div>
        </CanvasActionsProvider>
      </MeetingSourceProvider>
    </MeetingScopeProvider>
  );
}

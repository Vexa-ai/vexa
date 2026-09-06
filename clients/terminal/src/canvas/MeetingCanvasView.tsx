"use client";
import { useEffect, useRef } from "react";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { CanvasActionsProvider } from "./actions";
import { MeetingHealthBanner } from "./MeetingHealthBanner";
import { LiveTranscriptEngine } from "./LiveTranscriptEngine";
import { TranscriptExtend } from "./TranscriptExtend";
import { HighlightButton, useTermRenderer } from "./TranscriptTerms";
import { MeetingScopeProvider, MeetingSourceProvider, useMeeting } from "./useMeeting";

export const MEETING_CANVAS_CONTENT_INSET = 18;

/** ONE render engine, ONE source: the RAW transcript segments as they stream (PRD decision 34 —
 *  "the product runs no model calls of its own beside the agent"). There is no second, "cleaned"
 *  view to switch to any more, so there is no switch: the pane renders what the bot heard, and
 *  everything intelligent happens in the chat's agent over MCP. */
function RawTranscript({ meetingId }: { meetingId?: string }) {
  const { transcript } = useMeeting();
  // THE TERM CHIPS (PRD decision 35), as a layer over the same words. `useTermRenderer` returns
  // undefined until a Highlight has published something, so an un-highlighted meeting renders
  // exactly the plain text it did before — this costs nothing until somebody asks for it.
  //
  // It hangs on the RAW view deliberately: the raw transcript is the one that survives decision 34,
  // and it is the same component for a live meeting and a finished one, so chips work on both
  // without a second wiring.
  const renderText = useTermRenderer(meetingId ?? "");
  // THE SELECTION'S OWN BOX (Vexa-ai/vexa#1596). Two jobs, both of them this element's: it is what
  // "inside the transcript" MEANS for a selection — the rail, the chat and the banner are outside it
  // — and it is the positioning context the floating control sits in. `position: relative` and
  // nothing else; the engine below it renders exactly what it rendered before.
  const box = useRef<HTMLDivElement>(null);
  return (
    <div ref={box} style={{ position: "relative" }}>
      <LiveTranscriptEngine segments={transcript.segments} renderText={renderText} />
      {meetingId && <TranscriptExtend containerRef={box} meeting={meetingId} segments={transcript.segments} />}
    </div>
  );
}

function MeetingCanvasBody({ meetingId }: { meetingId?: string }) {
  const { meeting } = useMeeting();
  const layout = useService(LayoutServiceId);
  // Name the tab from the loaded meeting — a meeting opened by `?meeting=<id>` before the list loaded
  // opens as the generic "Meeting"; once the title arrives, rename the tab to match the list/prep views.
  useEffect(() => {
    if (meetingId && meeting.title && meeting.title !== "Meeting") {
      layout.setTitle(`meeting:${meetingId}`, meeting.title.split(" — ")[0]);
    }
  }, [layout, meetingId, meeting.title]);

  // Durable truth wins over a stale list `live` flag ([N8] — the row can stay stuck on a live
  // session_uid after a stop): once the effective status is TERMINAL the meeting is not
  // effectively-live, whatever the subscribe key says. Highlight is the only control that reads it.
  const durableTerminal = ["completed", "failed", "stopped", "past"].includes(meeting.status ?? "");
  const effectiveLive = meeting.live === true && !durableTerminal;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, background: "var(--bg)" }}>
      {/* ONE control, right-aligned. The processing toggle and its "cleaned + copilot" label stood
          here until PRD decision 34 removed the pipeline they switched. */}
      {meetingId && (
        <div style={{ flex: "none", display: "flex", alignItems: "center", justifyContent: "flex-end", padding: `8px ${MEETING_CANVAS_CONTENT_INSET}px 0` }}>
          {/* HIGHLIGHT (decision 35.2). Deliberately NOT gated on `effectiveLive`: the founder asked
              for "a button on transcripts", and a finished transcript is the one people actually read
              back. It needs the ROW id, which is what `meetingId` is here and what the tool takes. */}
          <HighlightButton meeting={meetingId} live={effectiveLive} />
        </div>
      )}
      <MeetingHealthBanner />
      <main style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <div style={{ padding: MEETING_CANVAS_CONTENT_INSET }}>
          <RawTranscript meetingId={meetingId} />
        </div>
      </main>
    </div>
  );
}

export function MeetingCanvasView({ meetingId }: { meetingId?: string }) {
  return (
    <MeetingScopeProvider meetingId={meetingId}>
      <MeetingSourceProvider meetingId={meetingId}>
        <CanvasActionsProvider>
          <MeetingCanvasBody meetingId={meetingId} />
        </CanvasActionsProvider>
      </MeetingSourceProvider>
    </MeetingScopeProvider>
  );
}

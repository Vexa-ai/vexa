"use client";
/** THE TRANSCRIPT AS A WIDGET INSIDE A PAGE (Vexa-ai/vexa#1598).
 *
 *  Founder, 2026-09-06, live: *"the right on meeting thing is a doc with the widget"*. A meeting has
 *  ONE page on the right — its own doc — and the transcript streams inside it where the doc's marker
 *  says it goes (`ui-kit/transcriptSlot.ts`). Not a second tab beside the page.
 *
 *  IT IS THE SAME ENGINE AND THE SAME SOURCE as the full canvas, deliberately: `MeetingCanvasView`
 *  and this file both mount the meeting providers and hand the segments to `LiveTranscriptEngine`
 *  (P23 — the ONE live-transcript render engine: it renders, it does not re-derive). So the term
 *  chips a Highlight publishes (#1595) paint here without a second wiring, and a fix to the engine
 *  is a fix to both. What differs is the FURNITURE, and only that: the canvas is a whole surface —
 *  its own header, its own scrollport, `height: 100%` — and a surface nested in a document is a pane
 *  inside a pane. This one flows with the page it is in, and scrolls with it.
 *
 *  Registered as a tab KIND (`surfaces/canvas.tsx`) rather than imported by the renderer, so the
 *  ui-kit keeps knowing nothing about meetings. */
import { useState } from "react";
import { copyText } from "../ui-kit/ContextMenu";
import { LiveTranscriptEngine } from "./LiveTranscriptEngine";
import { CanvasActionsProvider } from "./actions";
import { HighlightButton, useTermRenderer } from "./TranscriptTermControls";
import { MeetingScopeProvider, MeetingSourceProvider, useMeeting } from "./useMeeting";

function WidgetBody({ meetingId }: { meetingId: string }) {
  const { transcript, meeting } = useMeeting();
  const renderText = useTermRenderer(meetingId);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const copyTranscript = async () => {
    const segments = transcript.segments;
    const pending = [...segments].reverse().find(s => s.completed === false);
    const visible = [...segments.filter(s => s.completed !== false), ...(pending ? [pending] : [])];
    const text = visible.filter(s => s.text.trim()).map(s =>
      `${s.speaker ? s.speaker + "\n" : ""}${s.text}`).join("\n\n");
    setCopyError(false);
    try { await copyText(text); setCopied(true); }
    catch { setCopyError(true); }
  };
  // The same durable-truth rule the canvas states: a row can stay stuck on a live session_uid after
  // a stop, so a TERMINAL status wins over the list's `live` flag.
  const durableTerminal = ["completed", "failed", "stopped", "past"].includes(meeting.status ?? "");
  const live = meeting.live === true && !durableTerminal;
  return (
    <div data-transcript-widget={meetingId} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="t-xs fw-600 c-3" style={{ letterSpacing: 0.4, textTransform: "uppercase" }}>
          Transcript
        </span>
        {live && (
          <span data-widget-live className="t-xs c-accent" style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
            <span className="r-full bg-accent" style={{ width: 6, height: 6 }} />
            live
          </span>
        )}
        <span style={{ flex: "1 1 0%" }} />
        {/* Highlight belongs to the transcript, so it comes with it — a reader who scrolled the
            widget into view should not have to find a different surface to attribute what is in it. */}
        <button type="button" aria-label="Copy transcript" disabled={!transcript.segments.some(s => s.text.trim())}
          onClick={() => void copyTranscript()} onBlur={() => setCopied(false)}
          className="t-xs pt-0_5 pr-2 pb-0_5 pl-2 r-full bd-strong bg-3 c-2" style={{ cursor: "pointer" }}>
          {copied ? "Copied" : "Copy transcript"}
        </button>
        {copyError && <span role="alert">Could not copy transcript</span>}
        <HighlightButton meeting={meetingId} live={live} />
      </div>
      <LiveTranscriptEngine
        meetingId={meeting.id}
        segments={transcript.segments}
        renderText={renderText}
        emptyLabel={meeting.deleted
          ? "This meeting’s transcript and recording were deleted."
          : "Nothing said yet — this fills in as the room talks."}
      />
    </div>
  );
}

/** The widget, bound to one meeting ROW id — the same id the canvas binds to and the same id the
 *  doc's marker carries. */
export function MeetingTranscriptWidget({ meetingId }: { meetingId?: string }) {
  const id = String(meetingId ?? "").trim();
  if (!id) {
    // An honest empty state rather than a live-looking box bound to nothing: the marker named no
    // meeting, which is a defect in the page, not in the room.
    return <div className="c-3 t-xs">This page declares a transcript, but names no meeting.</div>;
  }
  return (
    <MeetingScopeProvider meetingId={id}>
      <MeetingSourceProvider meetingId={id}>
        <CanvasActionsProvider>
          <WidgetBody meetingId={id} />
        </CanvasActionsProvider>
      </MeetingSourceProvider>
    </MeetingScopeProvider>
  );
}

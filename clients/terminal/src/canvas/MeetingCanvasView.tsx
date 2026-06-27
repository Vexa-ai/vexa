"use client";
import { useState } from "react";
import { CanvasActionsProvider } from "./actions";
import { MeetingHealthBanner } from "./MeetingHealthBanner";
import { LiveTranscriptEngine } from "./LiveTranscriptEngine";
import { useMeetingNotes } from "./notes";
import { MeetingScopeProvider, MeetingSourceProvider, useMeeting } from "./useMeeting";

export const MEETING_CANVAS_CONTENT_INSET = 18;

/** ONE render engine for both modes (P23: the terminal RENDERS, it does not re-derive). The toggle picks
 *  the SOURCE — raw segments vs the cleaned processed mirror — not a different renderer. */
function RawTranscript() {
  const { transcript } = useMeeting();
  return <LiveTranscriptEngine segments={transcript.segments} />;
}

/** PROCESSED = the cleaned mirror + keyword tags (entities to research), through the SAME engine. */
function ProcessedTranscript() {
  const notes = useMeetingNotes();
  const segments = notes.map((n) => ({
    speaker: n.speaker, text: n.text, tsMs: n.tsMs, id: n.id, completed: n.completed,
    tags: (n.tags ?? []).map((t) => ({ label: t.label, kind: t.kind })),
  }));
  return <LiveTranscriptEngine segments={segments} emptyLabel="Processing transcript…" />;
}

export function MeetingCanvasView({ meetingId }: { meetingId?: string }) {
  // Default OFF → deliver the RAW transcript; the user flips it ON to get the processed canvas.
  const [processing, setProcessing] = useState(false);

  // The toggle ALSO controls backend processing: ON enables the copilot (full-history backfill the
  // first time, else resume); OFF disables it so nothing is processed. The raw transcript is unaffected.
  const toggleProcessing = () => {
    const next = !processing;
    setProcessing(next);
    if (meetingId) {
      void fetch("/api/meeting/process", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ native_id: meetingId, on: next }),
      }).catch(() => { /* best-effort — the view still reflects the toggle */ });
    }
  };

  return (
    <MeetingScopeProvider meetingId={meetingId}>
      <MeetingSourceProvider meetingId={meetingId}>
        <CanvasActionsProvider>
          <div style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, background: "var(--bg)" }}>
            <div style={{ flex: "none", display: "flex", alignItems: "center", gap: 10, padding: `8px ${MEETING_CANVAS_CONTENT_INSET}px 0` }}>
              <button
                type="button"
                onClick={toggleProcessing}
                aria-pressed={processing}
                title={processing ? "Showing the cleaned, copilot-processed view" : "Showing the raw transcript — flip on for processing"}
                style={{
                  display: "flex", alignItems: "center", gap: 7, cursor: "pointer",
                  background: processing ? "var(--accent)" : "transparent",
                  color: processing ? "#241008" : "var(--t2)",
                  border: `1px solid ${processing ? "var(--accent)" : "var(--line2)"}`,
                  borderRadius: 8, padding: "4px 10px", fontSize: 12, fontWeight: 600,
                }}
              >
                <span style={{ width: 7, height: 7, borderRadius: "50%", background: processing ? "#241008" : "var(--t3)", flex: "none" }} />
                Processing {processing ? "on" : "off"}
              </button>
              <span style={{ fontSize: 11.5, color: "var(--t3)" }}>{processing ? "cleaned + copilot" : "raw transcript"}</span>
            </div>
            <MeetingHealthBanner />
            <main style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              <div style={{ padding: MEETING_CANVAS_CONTENT_INSET }}>
                {processing ? <ProcessedTranscript /> : <RawTranscript />}
              </div>
            </main>
          </div>
        </CanvasActionsProvider>
      </MeetingSourceProvider>
    </MeetingScopeProvider>
  );
}

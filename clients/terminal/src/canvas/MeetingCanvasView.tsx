"use client";
import { useState } from "react";
import { CanvasActionsProvider } from "./actions";
import { useCanvasSource } from "./canvasSource";
import { MeetingHealthBanner } from "./MeetingHealthBanner";
import { CanvasRuntime } from "./runtime";
import { MeetingScopeProvider, MeetingSourceProvider, useMeeting } from "./useMeeting";

export const MEETING_CANVAS_CONTENT_INSET = 18;

/** RAW transcript — the unprocessed feed (no buildMeetingNotes clustering, no copilot cards), rendered
 *  as a CLEAN live transcript: CONFIRMED text is stable + append-only (consecutive same-speaker segments
 *  merge into one flowing block), and the in-flight PENDING text is a SINGLE dimmed "live" tail line.
 *  So the body never flickers while Whisper re-transcribes the unconfirmed window — only the one live
 *  edge line moves while a phrase forms. This is the meeting's DEFAULT; "Processing" on swaps in the
 *  cleaned, copilot-processed canvas. */
function RawTranscript() {
  const { transcript } = useMeeting();
  const segs = transcript.segments;

  // Confirmed (completed !== false) = stable. Merge consecutive same-speaker confirmed segments into
  // flowing blocks. Pending (completed === false) = the live, still-forming edge.
  const blocks: { speaker?: string; tsMs?: number; text: string; key: string }[] = [];
  for (const s of segs) {
    if (s.completed === false) continue;
    const last = blocks[blocks.length - 1];
    if (last && last.speaker === s.speaker) last.text += " " + s.text;
    else blocks.push({ speaker: s.speaker, tsMs: s.tsMs, text: s.text, key: s.id ?? `b${blocks.length}` });
  }
  const lastPending = [...segs].reverse().find((s) => s.completed === false);
  const live = (lastPending?.text ?? "").trim();
  const liveSpeaker = lastPending?.speaker;

  // The live (pending) text CONTINUES the last persisted block when it's the same speaker — it flows
  // inline (dimmed) as the still-forming tail of that paragraph, NOT a new speaker chapter. It only
  // opens its own block for a genuinely DIFFERENT speaker (or when there's no confirmed text yet).
  const lastBlock = blocks[blocks.length - 1];
  const liveJoinsLast = !!live && !!lastBlock && lastBlock.speaker === liveSpeaker;
  const liveOwnBlock = !!live && !liveJoinsLast;

  if (!blocks.length && !live) {
    return <div style={{ color: "var(--t3)", fontSize: 13, padding: "8px 2px" }}>Waiting for transcript…</div>;
  }

  const head = (speaker?: string, tsMs?: number) =>
    speaker ? (
      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--t2)", marginBottom: 3 }}>
        {speaker}
        {typeof tsMs === "number" && (
          <span style={{ fontWeight: 400, color: "var(--t3)", marginLeft: 8 }}>{new Date(tsMs).toLocaleTimeString()}</span>
        )}
      </div>
    ) : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 13, maxWidth: 760 }}>
      {blocks.map((b, idx) => (
        <div key={b.key}>
          {head(b.speaker, b.tsMs)}
          <div style={{ fontSize: 13.5, color: "var(--t1)", lineHeight: 1.6 }}>
            {b.text}
            {idx === blocks.length - 1 && liveJoinsLast && (
              <span style={{ color: "var(--t3)", fontStyle: "italic" }}> {live} …</span>
            )}
          </div>
        </div>
      ))}
      {liveOwnBlock && (
        <div>
          {head(liveSpeaker)}
          <div style={{ fontSize: 13.5, color: "var(--t3)", lineHeight: 1.6, fontStyle: "italic" }}>{live} …</div>
        </div>
      )}
    </div>
  );
}

export function MeetingCanvasView({ meetingId }: { meetingId?: string }) {
  const { source } = useCanvasSource();
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
                {processing ? <CanvasRuntime source={source} /> : <RawTranscript />}
              </div>
            </main>
          </div>
        </CanvasActionsProvider>
      </MeetingSourceProvider>
    </MeetingScopeProvider>
  );
}

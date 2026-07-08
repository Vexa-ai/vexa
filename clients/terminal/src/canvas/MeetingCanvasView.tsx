"use client";
import { useState } from "react";
import { CanvasActionsProvider, useActions, OPEN_ENTITY_EVENT } from "./actions";
import { MeetingHealthBanner } from "./MeetingHealthBanner";
import { LiveTranscriptEngine, type EngineActions, type EngineEntity, type EngineSignal } from "./LiveTranscriptEngine";
import { useMeetingNotes } from "./notes";
import { deriveProcessingView } from "./processingView";
import { MeetingScopeProvider, MeetingSourceProvider, useEntities, useMeeting, useSignals } from "./useMeeting";

export const MEETING_CANVAS_CONTENT_INSET = 18;

/** ONE render engine for both modes (P23: the terminal RENDERS, it does not re-derive). The toggle picks
 *  the SOURCE — raw segments vs the cleaned processed mirror — not a different renderer. */
function RawTranscript() {
  const { transcript } = useMeeting();
  return <LiveTranscriptEngine segments={transcript.segments} />;
}

// Map a copilot signal's loose context/kind onto the badge taxonomy (decision / action-item /
// question / claim). Unknown kinds fall through as-is so they still render with a neutral hue.
function signalKind(raw: string | undefined): string {
  const k = (raw ?? "").toLowerCase();
  if (/decis|commit/.test(k)) return "decision";
  if (/action|task|next.?step|follow/.test(k)) return "action-item";
  if (/question|ask|objection|concern/.test(k)) return "question";
  if (/claim|insight|fact/.test(k)) return "claim";
  return k || "signal";
}

/** PROCESSED v2 = the cleaned mirror with INLINE entity highlights (clickable → research / open entity
 *  doc) and actionable copilot SIGNAL badges, all through the SAME engine. */
function ProcessedTranscript() {
  const notes = useMeetingNotes();
  const entityItems = useEntities();
  const signalItems = useSignals();
  const actions = useActions();

  const segments = notes.map((n) => ({
    speaker: n.speaker, text: n.text, tsMs: n.tsMs, id: n.id, completed: n.completed,
    tags: (n.tags ?? []).map((t) => ({ label: t.label, kind: t.kind })),
  }));

  // Only highlight taggable entities (people/companies/products); numbers/signals aren't worth inline
  // wrapping in flowing prose. `splitTextIntoSpans` re-finds each label so the merge/tail stay intact.
  const entities: EngineEntity[] = entityItems
    .filter((e) => e.kind === "person" || e.kind === "company" || e.kind === "product")
    .map((e) => ({ id: e.id, label: e.name, kind: e.kind, docPath: e.docPath }));

  const signals: EngineSignal[] = signalItems.map((s) => ({ id: s.id, kind: signalKind(s.context), label: s.name }));

  const engineActions: EngineActions = {
    // Research in the VISIBLE chat (so the user sees it), not a hidden fire-and-forget session.
    research: (e) => actions.ask(`Research the ${e.kind} "${e.name}" using the web and my knowledge graph. Write or update its entity doc and commit, then summarize what you found.`),
    // Open the entity's doc — by its path if known, else resolve by name; reveals the center if needed.
    openEntityDoc: (e) => { if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { path: e.docPath, wikilink: e.name } })); },
    onSignal: (sig) => actions.ask(`Create a task / fact-check this ${sig.kind}: ${sig.label}`),
  };

  return (
    <LiveTranscriptEngine
      segments={segments}
      emptyLabel="Processing transcript…"
      entities={entities}
      signals={signals}
      actions={engineActions}
    />
  );
}

function MeetingCanvasBody({ meetingId }: { meetingId?: string }) {
  const { meeting, transcript } = useMeeting();
  const live = meeting.live === true;
  const hasNotes = (transcript.notes?.length ?? 0) > 0;
  // Durable truth wins over a stale list `live` flag ([N8] — the row can stay stuck on a live
  // session_uid after a stop): once the effective status is TERMINAL the meeting is not
  // effectively-live, whatever the subscribe key says. THE view-mode rule is the shared
  // deriveProcessingView (processingView.ts) — this component holds no private copy of it.
  const durableTerminal = ["completed", "failed", "stopped", "past"].includes(meeting.status ?? "");
  const effectiveLive = live && !durableTerminal;
  // P0: `meetingId` (the tab id) is the meetings-domain ROW id; the human-readable native is on the
  // meeting state — pass BOTH to /api/meeting/process so it keys the copilot on the row id (leak-safe)
  // while naming the kg doc by the native.
  const nativeId = meeting.nativeId;

  // null = untouched → follow the default (which can flip once the durable notes hydrate).
  const [override, setOverride] = useState<boolean | null>(null);
  const processing = deriveProcessingView({ override, live, hasNotes, durableTerminal });

  // LIVE: the toggle ALSO controls backend processing — ON enables the copilot (full-history backfill
  // the first time, else resume); OFF disables it. COMPLETED: pure view switch — there is nothing to
  // arm any more, so we must NOT hit the process endpoint (a stale-live row counts as completed).
  const toggleProcessing = () => {
    const next = !processing;
    setOverride(next);
    if (meetingId && effectiveLive) {
      void fetch("/api/meeting/process", {
        method: "POST", headers: { "Content-Type": "application/json" },
        // meeting_id = the ROW id (the copilot keys on it); native_id = the display native (kg doc name).
        body: JSON.stringify({ meeting_id: meetingId, native_id: nativeId ?? meetingId, on: next }),
      }).catch(() => { /* best-effort — the view still reflects the toggle */ });
    }
  };

  // Completed meetings get view names (nothing is "processing" any more); live keeps the arm/disarm wording.
  const label = effectiveLive ? `Processing ${processing ? "on" : "off"}` : (processing ? "Processed" : "Raw");

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, background: "var(--bg)" }}>
      <div style={{ flex: "none", display: "flex", alignItems: "center", gap: 10, padding: `8px ${MEETING_CANVAS_CONTENT_INSET}px 0` }}>
        <button
          type="button"
          onClick={toggleProcessing}
          aria-pressed={processing}
          title={processing ? "Showing the cleaned, copilot-processed view" : (effectiveLive ? "Showing the raw transcript — flip on for processing" : "Showing the raw transcript")}
          style={{
            display: "flex", alignItems: "center", gap: 7, cursor: "pointer",
            background: processing ? "var(--accent)" : "transparent",
            color: processing ? "#241008" : "var(--t2)",
            border: `1px solid ${processing ? "var(--accent)" : "var(--line2)"}`,
            borderRadius: 8, padding: "4px 10px", fontSize: 12, fontWeight: 600,
          }}
        >
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: processing ? "#241008" : "var(--t3)", flex: "none" }} />
          {label}
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

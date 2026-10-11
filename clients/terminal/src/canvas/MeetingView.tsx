"use client";
/** THE MEETING VIEW — one page per meeting, the same wherever it is opened (founder-approved design,
 *  2026-10-11: *"looks correct"* on the mockup).
 *
 *    1. HEADER — `MeetingPageHeader`: the title, platform · date · people (· live · N min), the
 *       recording player when it is over, and the meeting's own controls. It is the seam the header
 *       work lands through (rename, share, export, audio download all extend that component), so
 *       those controls arrive here without this file changing.
 *    2. NOTES — the meeting's markdown file, rendered. agent-api names it, creates it and is its
 *       only writer; **Update notes** is the existing Extend act on the meeting's page, and
 *       **Create notes** is the existing mint. No notes yet is the same layout with a placeholder:
 *       the view never needs the file to exist to open the meeting.
 *    3. TRANSCRIPT — meeting-api's, from its database and live stream, never copied into the notes.
 *       Collapsed by default (live or finished) to its last few lines, which keep moving while the
 *       meeting runs; expanded, it is the whole thing with Highlight, Ask about this and
 *       click-to-play. The person's toggle is remembered per meeting for the session.
 *
 *  The chat bound to the meeting stays beside this view; nothing here replaces it. */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { WORKSPACE_COMMIT_EVENT, useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { LiveTranscriptEngine } from "./LiveTranscriptEngine";
import { CanvasActionsProvider } from "./actions";
import { MeetingHealthBanner } from "./MeetingHealthBanner";
import { TranscriptExtend } from "./TranscriptExtend";
import { HighlightButton, useTermRenderer } from "./TranscriptTermControls";
import { MeetingScopeProvider, MeetingSourceProvider, useMeeting } from "./useMeeting";
import { createStickToBottom } from "../surfaces/stickToBottom";
import { actWords } from "../surfaces/actState";
import { readWorkspaceFile, readWorkspaceHistory } from "../surfaces/workspaceApi";
import { Icon } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import { DocMetaContext } from "../ui-kit/docRefs";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { useFiredAct } from "../minutes/ExtendAction";
import { MeetingPageHeader } from "../minutes/MeetingPageHeader";
import { fetchMeetingNote } from "../minutes/meetingNote";
import {
  createMeetingNotes, loadTranscriptOpen, notesForView, notesFreshness, saveTranscriptOpen,
  transcriptLines, transcriptTail, updateMeetingNotes,
} from "./meetingNotes";
import { surface, type as ty } from "../minutes/tokens";
import { DESK_SLUG } from "../minutes/workspaceReadme";

export const MEETING_VIEW_INSET = 18;

type Notes =
  | { phase: "loading" }
  | { phase: "none" }
  | { phase: "error"; message: string }
  | { phase: "ready"; path: string; body: string; cursor: string; updatedAt: number | null };

/** The notes file, read the way every other page on the desk is read — and re-read whenever a turn
 *  commits to the workspace, which is how an Update (or a change asked for in the chat) shows up
 *  without the person reloading anything. A re-read keeps what is on screen until the new answer
 *  arrives, so the notes never flash to a spinner under the reader. */
export function useMeetingNotes(meetingId: string): Notes & { reload: () => void } {
  const [notes, setNotes] = useState<Notes>({ phase: "loading" });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let dead = false;
    void (async () => {
      const note = await fetchMeetingNote(meetingId);          // never throws; null path = no notes
      if (dead) return;
      if (!note.path) { setNotes({ phase: "none" }); return; }
      let body: string | null;
      try { body = await readWorkspaceFile(note.path); }
      catch { if (!dead) setNotes({ phase: "error", message: "Could not load the notes. Retry." }); return; }
      if (dead) return;
      // named on the row but not on this desk: there are no notes HERE, and Create puts them here
      if (body === null) { setNotes({ phase: "none" }); return; }
      let updatedAt: number | null = null;
      try {
        const h = await readWorkspaceHistory(DESK_SLUG, { path: note.path, limit: 1 });
        updatedAt = h.commits[0]?.ts || null;
      } catch { /* the time is a nicety; the notes are not withheld for it */ }
      if (!dead) setNotes({ phase: "ready", path: note.path, body, cursor: note.cursor, updatedAt });
    })();
    return () => { dead = true; };
  }, [meetingId, nonce]);
  useEffect(() => {
    const onCommit = () => setNonce((n) => n + 1);
    window.addEventListener(WORKSPACE_COMMIT_EVENT, onCommit);
    return () => window.removeEventListener(WORKSPACE_COMMIT_EVENT, onCommit);
  }, []);
  return { ...notes, reload: () => setNonce((n) => n + 1) };
}

const sectionHead: CSSProperties = { display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8, rowGap: 6, minWidth: 0 };
const label: CSSProperties = { ...ty.bodyStrong, color: "var(--t1)", margin: 0, fontSize: 13 };
const quiet: CSSProperties = {
  ...ty.chip, display: "inline-flex", alignItems: "center", gap: 6, color: "var(--t2)",
  background: "transparent", border: "1px solid var(--line)", borderRadius: 6, padding: "4px 10px", cursor: "pointer",
};
const primary: CSSProperties = { ...quiet, color: "var(--accent)", borderColor: "var(--accent)", background: "var(--accentbg)", fontWeight: 600 };

/** The notes' one button — Update or Create — wearing the state of the act it fired: the same
 *  `useFiredAct` the page's own Extend control uses, so "is it doing anything?" is answered where it
 *  was asked, and a second press while it runs is not offered. */
function NotesAct(p: { kind: "update" | "create"; slot: string; disabled?: boolean; onFire: () => Promise<ReturnType<typeof updateMeetingNotes>> }) {
  const { state, remember, forget } = useFiredAct(p.slot);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const title = p.kind === "update" ? "Update notes" : "Create notes";
  if ((state && state.phase !== "failed") || busy) {
    const w = state ? actWords(state, title) : { head: "Creating notes…", line: "" };
    return <span data-notes-act={p.kind} data-act-state={state?.phase ?? "creating"} role="status" aria-live="polite" aria-busy="true"
      style={{ ...ty.meta, display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span className="vx-op-spin" aria-hidden="true" style={{ width: 12, height: 12, borderRadius: "50%", border: "1.5px solid var(--line2)", borderTopColor: "var(--accent)" }} />
      {w.head}{w.line ? ` · ${w.line}` : ""}
    </span>;
  }
  const failed = state?.phase === "failed" ? actWords(state, title).line : error;
  return <>
    <button type="button" data-notes-act={p.kind} disabled={p.disabled}
      title={p.kind === "update" ? "Rewrite these notes from the transcript, in this chat" : "Create this meeting's notes file, then write it from the transcript"}
      onClick={async () => {
        forget(); setError(""); setBusy(true);
        try { remember(await p.onFire()); }
        catch (e) { setError(e instanceof Error ? e.message : "Could not do that. Retry."); }
        finally { setBusy(false); }
      }}
      style={p.kind === "create" ? primary : quiet}>
      <Icon name={p.kind === "create" ? "plus" : "refresh"} size={13} /> {title}
    </button>
    {failed && <span role="alert" style={{ ...ty.meta, color: "var(--danger)" }}>{failed}</span>}
  </>;
}

function NotesSection({ meetingId, notes, lines }: { meetingId: string; notes: ReturnType<typeof useMeetingNotes>; lines: number }) {
  const { segments } = useMeeting().transcript;
  const update = async (path: string) => updateMeetingNotes(meetingId, path);
  // CREATE IS THE MINT, THEN THE SAME UPDATE. The mint (agent-api, idempotent) puts the page on the
  // desk; the view renders it at once; and when there is anything said yet the same Extend act fills
  // it from the transcript — one press, one writer, nothing invented here.
  const create = async () => {
    const path = await createMeetingNotes(meetingId);
    notes.reload();
    return lines > 0 ? updateMeetingNotes(meetingId, path) : null;
  };
  return (
    <section data-meeting-notes aria-labelledby={`notes-${meetingId}`} style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
      <div style={sectionHead}>
        <h2 id={`notes-${meetingId}`} style={label}>Notes</h2>
        {notes.phase === "ready" && <span data-notes-path title={notes.path}
          style={{ ...ty.mono, fontSize: 11, minWidth: 0, flex: "0 1 auto", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{notes.path}</span>}
        <span style={{ flex: "1 1 0%" }} />
        {notes.phase === "ready" && <NotesAct kind="update" slot={`notes|${notes.path}`} onFire={() => update(notes.path)} />}
      </div>
      {notes.phase === "ready" && <div data-notes-freshness style={ty.meta}>
        {notesFreshness({ updatedAt: notes.updatedAt, cursor: notes.cursor, segments })}
      </div>}
      {notes.phase === "loading" && <div role="status" style={ty.meta}>Loading notes…</div>}
      {notes.phase === "error" && <div role="alert" style={{ ...ty.meta, color: "var(--danger)" }}>
        {notes.message} <button type="button" style={quiet} onClick={notes.reload}>Retry</button>
      </div>}
      {notes.phase === "none" && (
        <div data-notes-empty style={{
          display: "flex", alignItems: "center", flexWrap: "wrap", gap: 10, padding: "14px 16px",
          border: "1px dashed var(--line2)", borderRadius: 8, color: "var(--t3)",
        }}>
          <span style={{ ...ty.body, color: "var(--t2)", flex: "1 1 12em" }}>No notes for this meeting yet</span>
          <NotesAct kind="create" slot={`notes-create|${meetingId}`} onFire={create} />
        </div>
      )}
      {notes.phase === "ready" && (
        <div data-notes-body style={{ ...ty.body, lineHeight: 1.6, color: "var(--t1)", minWidth: 0 }}>
          <DocMetaContext.Provider value={{ path: notes.path }}>
            <MdxDoc>{notesForView(notes.body)}</MdxDoc>
          </DocMetaContext.Provider>
        </div>
      )}
    </section>
  );
}

function TranscriptSection({ meetingId, scroller }: { meetingId: string; scroller: () => HTMLElement | null }) {
  const { transcript, meeting } = useMeeting();
  const renderText = useTermRenderer(meetingId);
  const [open, setOpen] = useState(() => loadTranscriptOpen(meetingId));
  useEffect(() => { setOpen(loadTranscriptOpen(meetingId)); }, [meetingId]);
  const toggle = () => setOpen((v) => { saveTranscriptOpen(meetingId, !v); return !v; });
  const segments = transcript.segments;
  const lines = transcriptLines(segments);
  const shown = open ? segments : transcriptTail(segments);
  const durableTerminal = ["completed", "failed", "stopped", "past"].includes(meeting.status ?? "");
  const live = meeting.live === true && !durableTerminal;
  const box = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  // EXPANDED AND LIVE, the view follows the newest line — unless the person has scrolled up to read,
  // which `stickToBottom` decides from the scroll position, never from a late event.
  const follower = useMemo(() => createStickToBottom(() => scroller()), [scroller]);
  useEffect(() => { if (open && live) follower.onContent(); }, [open, live, segments.length, follower]);
  useEffect(() => {
    const el = scroller();
    if (!el) return;
    const on = () => follower.onScroll();
    el.addEventListener("scroll", on);
    return () => el.removeEventListener("scroll", on);
  }, [scroller, follower]);
  const copy = async () => {
    const text = segments.filter((s) => s.completed !== false && s.text.trim())
      .map((s) => `${s.speaker ? s.speaker + "\n" : ""}${s.text}`).join("\n\n");
    try { await copyText(text); setCopied(true); } catch { setCopied(false); }
  };
  const body = `transcript-${meetingId}`;
  return (
    <section data-meeting-transcript data-open={open ? "" : undefined} aria-label="Transcript"
      style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0, borderTop: "1px solid var(--line)", paddingTop: 14 }}>
      <div style={sectionHead}>
        <button type="button" data-transcript-toggle aria-expanded={open} aria-controls={body} onClick={toggle}
          title={open ? "Show only the latest lines" : "Show the whole transcript"}
          style={{ ...label, display: "inline-flex", alignItems: "center", gap: 6, background: "transparent", border: "none", padding: 0, cursor: "pointer" }}>
          {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
          Transcript · {lines} {lines === 1 ? "line" : "lines"}
        </button>
        {live && <span data-transcript-live style={{ ...ty.meta, display: "inline-flex", alignItems: "center", gap: 5, color: "var(--accent)" }}>
          <span aria-hidden style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--accent)" }} />live</span>}
        <span style={{ flex: "1 1 0%" }} />
        {open && <>
          <button type="button" aria-label="Copy transcript" disabled={!lines} onClick={() => void copy()} onBlur={() => setCopied(false)} style={quiet}>
            {copied ? "Copied" : "Copy transcript"}
          </button>
          <HighlightButton meeting={meetingId} live={live} />
        </>}
      </div>
      <div id={body} ref={box} style={{ position: "relative", minWidth: 0 }}>
        <LiveTranscriptEngine meetingId={meeting.id} segments={shown} renderText={renderText}
          emptyLabel={meeting.deleted ? "This meeting’s transcript and recording were deleted." : "Nothing said yet — this fills in as the room talks."} />
        {open && <TranscriptExtend containerRef={box} meeting={meetingId} segments={segments} />}
      </div>
    </section>
  );
}

function MeetingViewBody({ meetingId }: { meetingId: string }) {
  const notes = useMeetingNotes(meetingId);
  const { transcript, meeting } = useMeeting();
  const layout = useService(LayoutServiceId);
  // Name the workbench tab from the loaded meeting, as the canvas did: a meeting opened by link
  // before the list loaded opens as "Meeting" and takes its title once the row arrives.
  useEffect(() => {
    if (meeting.title && meeting.title !== "Meeting") layout.setTitle(`meeting:${meetingId}`, meeting.title.split(" — ")[0]);
  }, [layout, meetingId, meeting.title]);
  const scroll = useRef<HTMLDivElement>(null);
  const scroller = useMemo(() => () => scroll.current, []);
  return (
    <div data-meeting-view={meetingId} style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, minWidth: 0, background: surface.pages }}>
      <div ref={scroll} style={{ flex: 1, minHeight: 0, overflowY: "auto", overflowX: "hidden" }}>
        <header style={{ padding: `14px ${MEETING_VIEW_INSET}px 12px`, borderBottom: "1px solid var(--line)", minWidth: 0 }}>
          <MeetingPageHeader meetingId={meetingId} body={notes.phase === "ready" ? notes.body : ""} path={notes.phase === "ready" ? notes.path : ""} />
        </header>
        <MeetingHealthBanner />
        <div style={{ padding: `16px ${MEETING_VIEW_INSET}px 32px`, display: "flex", flexDirection: "column", gap: 20, minWidth: 0 }}>
          <NotesSection meetingId={meetingId} notes={notes} lines={transcriptLines(transcript.segments)} />
          <TranscriptSection meetingId={meetingId} scroller={scroller} />
        </div>
      </div>
    </div>
  );
}

/** The view, bound to one meeting ROW id. */
export function MeetingView({ meetingId }: { meetingId: string }) {
  return (
    <MeetingScopeProvider meetingId={meetingId}>
      <MeetingSourceProvider meetingId={meetingId}>
        <CanvasActionsProvider>
          <MeetingViewBody meetingId={meetingId} />
        </CanvasActionsProvider>
      </MeetingSourceProvider>
    </MeetingScopeProvider>
  );
}

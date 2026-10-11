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
 *       Its own panel, never prose. Collapsed (the default, live or finished) it is a scrolling
 *       window about four lines tall under the notes, sitting on the newest line. Expanded
 *       ("Show all") it moves into a narrow, resizable sidebar on the view's right — or, where the
 *       view is too narrow for one, a full-width sheet over the notes. Playback highlights the line
 *       being played and keeps it in view in every mode. The choice and the width are remembered
 *       for the session.
 *
 *  The chat bound to the meeting stays beside this view; nothing here replaces it. */
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { ChevronDown, MessageSquareText, PanelRightOpen } from "lucide-react";
import { WORKSPACE_COMMIT_EVENT, useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { LiveTranscriptEngine } from "./LiveTranscriptEngine";
import { CanvasActionsProvider } from "./actions";
import { MeetingHealthBanner } from "./MeetingHealthBanner";
import { TranscriptExtend } from "./TranscriptExtend";
import { HighlightButton, useTermRenderer } from "./TranscriptTermControls";
import { MeetingScopeProvider, MeetingSourceProvider, useMeeting } from "./useMeeting";
import { useMeetingPlayback } from "../minutes/meetingPlayback";
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
  transcriptLines, updateMeetingNotes, withoutEmptySections,
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
            <MdxDoc>{withoutEmptySections(notesForView(notes.body))}</MdxDoc>
          </DocMetaContext.Provider>
        </div>
      )}
    </section>
  );
}

/** The collapsed panel's window: about four lines of the conversation. */
export const TRANSCRIPT_WINDOW_PX = 128;
/** The expanded transcript's sidebar: narrow, resizable within these bounds, remembered per session. */
export const SIDEBAR = { min: 260, max: 480, initial: 320 } as const;
/** Below this view width the sidebar cannot sit beside the notes, so the expanded transcript is a
 *  full-width sheet over them instead. */
export const SIDEBAR_FITS_PX = 640;
const WIDTH_KEY = "vexa.meetingTranscriptWidth";
const loadWidth = (): number => {
  try { const n = Number(sessionStorage.getItem(WIDTH_KEY)); return n >= SIDEBAR.min && n <= SIDEBAR.max ? n : SIDEBAR.initial; }
  catch { return SIDEBAR.initial; }
};
const saveWidth = (n: number) => { try { sessionStorage.setItem(WIDTH_KEY, String(n)); } catch { /* storage refused */ } };

/** Where a scroller must sit to put a line in the middle of it — clamped to what it can scroll. */
export function centeredTop(box: { scrollHeight: number; clientHeight: number }, lineTop: number, lineHeight: number): number {
  const want = lineTop - (box.clientHeight - lineHeight) / 2;
  return Math.max(0, Math.min(want, Math.max(0, box.scrollHeight - box.clientHeight)));
}

/** Where the transcript is drawn: the scrolling TAIL at the foot of the meeting view (collapsed), a
 *  narrow SIDEBAR on the view's right (expanded, wide enough), or a full-width SHEET over the notes
 *  (expanded, too narrow for a sidebar). */
export type TranscriptMode = "tail" | "sidebar" | "sheet";

/** THE TRANSCRIPT, a different object from the notes (founder, 2026-10-11: *"this should be distinct
 *  it's transcript here - it's not evident from the UX"*): its own surface, its own header (what it
 *  is, how long, whether it is live), conversational rows. In every mode the lines live in ONE
 *  scrolling window, so following works the same everywhere: it sits on the newest line until the
 *  person scrolls up (then *Jump to latest*), and during playback it keeps the played line in view
 *  until the person scrolls away (then *Follow playback*). A scroll this panel made itself is
 *  recognised by where it left the box, so only a person's scroll lets go. */
function TranscriptPanel({ meetingId, mode, onToggle }: { meetingId: string; mode: TranscriptMode; onToggle: () => void }) {
  const { transcript, meeting } = useMeeting();
  const renderText = useTermRenderer(meetingId);
  const playback = useMeetingPlayback(meeting.id);
  const open = mode !== "tail";
  const segments = transcript.segments;
  const lines = transcriptLines(segments);
  const durableTerminal = ["completed", "failed", "stopped", "past"].includes(meeting.status ?? "");
  const live = meeting.live === true && !durableTerminal;
  const box = useRef<HTMLDivElement>(null);       // the lines' own box (selection scope)
  const win = useRef<HTMLDivElement>(null);       // the window that scrolls
  const [copied, setCopied] = useState(false);
  const playing = playback.time !== null;
  const [followLatest, setFollowLatest] = useState(true);
  const [followPlay, setFollowPlay] = useState(true);
  const [above, setAbove] = useState(false);      // lines above the window's top edge
  const lastTop = useRef(-1);
  const setTop = (el: HTMLElement, top: number) => { el.scrollTop = top; lastTop.current = el.scrollTop; setAbove(el.scrollTop > 2); };
  const toBottom = () => { const el = win.current; if (el) setTop(el, el.scrollHeight); };
  const toActive = (): boolean => {
    const el = win.current;
    const line = box.current?.querySelector<HTMLElement>("[data-playback-active]");
    if (!el || !line) return false;
    const lr = line.getBoundingClientRect(), br = el.getBoundingClientRect();
    setTop(el, centeredTop(el, lr.top - br.top + el.scrollTop, lr.height));
    return true;
  };
  // a new playback follows again from the start
  useEffect(() => { if (playing) setFollowPlay(true); }, [playing]);
  useEffect(() => {
    if (playing && followPlay && toActive()) return;
    if (!playing && followLatest) toBottom();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, segments.length, playback.time, followPlay, followLatest]);
  const onScroll = () => {
    const el = win.current;
    if (!el || Math.abs(el.scrollTop - lastTop.current) <= 2) return;
    lastTop.current = el.scrollTop;
    setAbove(el.scrollTop > 2);
    setFollowLatest(el.scrollHeight - el.scrollTop - el.clientHeight <= 8);
    if (playing) setFollowPlay(false);
  };
  const copy = async () => {
    const text = segments.filter((s) => s.completed !== false && s.text.trim())
      .map((s) => `${s.speaker ? s.speaker + "\n" : ""}${s.text}`).join("\n\n");
    try { await copyText(text); setCopied(true); } catch { setCopied(false); }
  };
  const body = `transcript-${meetingId}`;
  const frame: CSSProperties = mode === "tail"
    ? { background: surface.raised, border: "1px solid var(--line)", borderRadius: 12, padding: "12px 14px 14px" }
    : { background: surface.raised, height: "100%", padding: "12px 12px 0", boxSizing: "border-box" };
  return (
    <section data-meeting-transcript data-mode={mode} role="region" aria-labelledby={`${body}-title`} data-open={open ? "" : undefined}
      style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0, minHeight: 0, ...frame }}>
      <div style={sectionHead}>
        <span style={{ display: "inline-flex", color: "var(--t2)" }}><MessageSquareText size={15} aria-hidden /></span>
        <h2 id={`${body}-title`} style={{ ...label, fontSize: 13 }}>Transcript</h2>
        <span data-transcript-count style={{ ...ty.meta, fontVariantNumeric: "tabular-nums" }}>{lines} {lines === 1 ? "line" : "lines"}</span>
        {live && <span data-transcript-live style={{ ...ty.meta, display: "inline-flex", alignItems: "center", gap: 5, color: "var(--accent)" }}>
          <span aria-hidden style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--accent)" }} />live</span>}
        <span style={{ flex: "1 1 0%" }} />
        <button type="button" data-transcript-toggle aria-expanded={open} aria-controls={body} onClick={onToggle}
          title={open ? "Back to the latest lines under the notes" : "Open the whole transcript beside the notes"} style={quiet}>
          {open ? <ChevronDown size={13} aria-hidden /> : <PanelRightOpen size={13} aria-hidden />}
          {open ? "Collapse" : "Show all"}
        </button>
      </div>
      {(open || (playing && !followPlay) || (!playing && !followLatest)) && <div style={{ ...sectionHead, gap: 6 }}>
        {playing && !followPlay && <button type="button" data-transcript-follow-playback style={quiet}
          onClick={() => { setFollowPlay(true); toActive(); }}>Follow playback</button>}
        {!playing && !followLatest && <button type="button" data-transcript-jump style={quiet}
          onClick={() => { setFollowLatest(true); toBottom(); }}>Jump to latest</button>}
        {open && <>
          <span style={{ flex: "1 1 0%" }} />
          <button type="button" aria-label="Copy transcript" disabled={!lines} onClick={() => void copy()} onBlur={() => setCopied(false)} style={quiet}>
            {copied ? "Copied" : "Copy transcript"}
          </button>
          <HighlightButton meeting={meetingId} live={live} />
        </>}
      </div>}
      <div style={{ position: "relative", minWidth: 0, minHeight: 0, flex: open ? "1 1 0%" : "none", display: "flex", flexDirection: "column" }}>
        <div id={body} ref={win} data-transcript-window tabIndex={0} aria-label="Transcript lines" onScroll={onScroll}
          style={open
            ? { minWidth: 0, flex: "1 1 0%", minHeight: 0, overflowY: "auto", overscrollBehavior: "contain", paddingBottom: 12 }
            : { minWidth: 0, maxHeight: TRANSCRIPT_WINDOW_PX, overflowY: "auto", overscrollBehavior: "contain" }}>
          <div ref={box} style={{ position: "relative", minWidth: 0 }}>
            <LiveTranscriptEngine meetingId={meeting.id} segments={segments} renderText={renderText} layout="rows"
              emptyLabel={meeting.deleted ? "This meeting’s transcript and recording were deleted." : "Nothing said yet — this fills in as the room talks."} />
            <TranscriptExtend containerRef={box} meeting={meetingId} segments={segments} />
          </div>
        </div>
        {/* there is more above: a fade says so without a word */}
        {above && <div data-transcript-fade aria-hidden style={{ position: "absolute", left: 0, right: 0, top: 0, height: 24, pointerEvents: "none",
          background: `linear-gradient(${surface.raised}, transparent)` }} />}
      </div>
    </section>
  );
}

/** The drag handle between the notes and the transcript sidebar: pointer drag, or the arrow keys
 *  when focused (16px a press). */
function SidebarHandle({ width, onWidth }: { width: number; onWidth: (n: number) => void }) {
  const clamp = (n: number) => Math.max(SIDEBAR.min, Math.min(SIDEBAR.max, Math.round(n)));
  return <div role="separator" aria-orientation="vertical" aria-label="Resize the transcript" tabIndex={0}
    aria-valuemin={SIDEBAR.min} aria-valuemax={SIDEBAR.max} aria-valuenow={width} data-transcript-resize
    onKeyDown={(e) => {
      if (e.key === "ArrowLeft") { e.preventDefault(); onWidth(clamp(width + 16)); }
      if (e.key === "ArrowRight") { e.preventDefault(); onWidth(clamp(width - 16)); }
    }}
    onPointerDown={(e) => {
      const x0 = e.clientX, w0 = width;
      const move = (ev: PointerEvent) => onWidth(clamp(w0 + (x0 - ev.clientX)));
      const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
      window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
    }}
    style={{ flex: "none", width: 6, cursor: "col-resize", borderLeft: "1px solid var(--line)", background: "transparent" }} />;
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
  // COLLAPSED BY DEFAULT, live or finished; the person's choice is remembered per meeting.
  const [open, setOpen] = useState(() => loadTranscriptOpen(meetingId));
  useEffect(() => { setOpen(loadTranscriptOpen(meetingId)); }, [meetingId]);
  const toggle = () => setOpen((v) => { saveTranscriptOpen(meetingId, !v); return !v; });
  const [width, setWidth] = useState(loadWidth);
  const onWidth = (n: number) => { setWidth(n); saveWidth(n); };
  // How wide the VIEW is, not the window: the view sits in a resizable column.
  const root = useRef<HTMLDivElement>(null);
  const [viewW, setViewW] = useState(0);
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const read = () => setViewW(el.clientWidth);
    read();
    const RO = (globalThis as { ResizeObserver?: typeof ResizeObserver }).ResizeObserver;
    if (RO) { const ro = new RO(read); ro.observe(el); return () => ro.disconnect(); }
    window.addEventListener("resize", read);
    return () => window.removeEventListener("resize", read);
  }, []);
  // 0 = not measured (no layout yet): assume the sidebar fits rather than cover the notes
  const fits = viewW === 0 || viewW >= SIDEBAR_FITS_PX;
  const mode: TranscriptMode = !open ? "tail" : fits ? "sidebar" : "sheet";
  return (
    <div ref={root} data-meeting-view={meetingId} style={{ position: "relative", height: "100%", display: "flex", flexDirection: "row", minHeight: 0, minWidth: 0, background: surface.pages }}>
      <div data-meeting-main style={{ flex: "1 1 0%", minWidth: 0, minHeight: 0, overflowY: "auto", overflowX: "hidden" }}>
        <header style={{ padding: `14px ${MEETING_VIEW_INSET}px 12px`, borderBottom: "1px solid var(--line)", minWidth: 0 }}>
          <MeetingPageHeader meetingId={meetingId} body={notes.phase === "ready" ? notes.body : ""} path={notes.phase === "ready" ? notes.path : ""} />
        </header>
        <MeetingHealthBanner />
        <div style={{ padding: `16px ${MEETING_VIEW_INSET}px 32px`, display: "flex", flexDirection: "column", gap: 20, minWidth: 0 }}>
          <NotesSection meetingId={meetingId} notes={notes} lines={transcriptLines(transcript.segments)} />
          {/* the tail sits under the notes only while collapsed — opened, it moves to the side */}
          {mode === "tail" && <TranscriptPanel meetingId={meetingId} mode="tail" onToggle={toggle} />}
        </div>
      </div>
      {mode === "sidebar" && <>
        <SidebarHandle width={width} onWidth={onWidth} />
        <aside data-transcript-sidebar style={{ flex: "none", width, minWidth: 0, minHeight: 0, height: "100%" }}>
          <TranscriptPanel meetingId={meetingId} mode="sidebar" onToggle={toggle} />
        </aside>
      </>}
      {mode === "sheet" && <div data-transcript-sheet style={{ position: "absolute", inset: 0, zIndex: 5, background: surface.raised }}>
        <TranscriptPanel meetingId={meetingId} mode="sheet" onToggle={toggle} />
      </div>}
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

"use client";
/** THE MEETING VIEW — one page per meeting, the same wherever it is opened (founder-approved design,
 *  2026-10-11: *"looks correct"* on the mockup).
 *
 *    1. HEADER — `MeetingViewHeader` (#1783): the meeting's real name, when · how long · who, live or
 *       ended, and ONE row of acts (Play recording · Share · Update notes · Delete, the rest behind
 *       "…"); the recording player sits under it, inline. No path is ever shown.
 *    2. NOTES — the meeting's markdown file, rendered. agent-api names it, creates it and is its
 *       only writer; **Update notes** is the existing Extend act on the meeting's page, and
 *       **Create notes** is the existing mint. No notes yet is the same layout with a placeholder:
 *       the view never needs the file to exist to open the meeting.
 *    3. TRANSCRIPT — meeting-api's, from its database and live stream, never copied into the notes.
 *       A DRAWER AT THE FOOT (founder, UI lab 2026-10-14): collapsed it is one line — "Transcript ·
 *       N lines · ● live" and the latest line; opened it grows upward over the note, with search,
 *       following the newest line while live, and a click on a line seeks the recording. The choice
 *       is remembered per meeting for the session.
 *
 *  THREE ZONES: the meeting's tools stay fixed on top, the note scrolls in the middle, the drawer
 *  sits at the bottom. The meeting is the primary object; its note is embedded, editable in place,
 *  and opens as a page.
 *
 *  The chat bound to the meeting stays beside this view; nothing here replaces it. */
import { SelectionAct, referenceInChat } from "../minutes/ExtendAction";
import "./meetingView.css";
import { ChatAboutButton } from "../minutes/shell2/ChatAbout";
import { meetingDisplayName } from "../minutes/callsList";
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { ChevronDown, ChevronUp, Copy, Check, MessageSquareText, Search, X } from "lucide-react";
import { WORKSPACE_COMMIT_EVENT, useService } from "../platform";
import { LayoutServiceId } from "../platform/layout";
import { LiveTranscriptEngine } from "./LiveTranscriptEngine";
import { CanvasActionsProvider } from "./actions";
import { MeetingHealthBanner } from "./MeetingHealthBanner";
import { TranscriptExtend } from "./TranscriptExtend";
import { HighlightButton, useTermRenderer } from "./TranscriptTermControls";
import { MeetingScopeProvider, MeetingSourceProvider, useMeeting } from "./useMeeting";
import { useMeetingPlayback } from "../minutes/meetingPlayback";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import { actWords } from "../surfaces/actState";
import { readWorkspaceFile, readWorkspaceHistory, writeWorkspaceFile } from "../data/workspaceApi";
import { MarkdownEditor } from "../minutes/MarkdownEditor";
import { Icon } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import { DocMetaContext } from "../ui-kit/docRefs";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { useFiredAct } from "../minutes/ExtendAction";
import { MeetingControls } from "../minutes/MeetingControls";
import { createMeetingPage, fetchMeetingNote } from "../minutes/meetingNote";
import { MeetingViewHeader } from "./MeetingViewHeader";
import { editableNote } from "../minutes/noteMarkers";
import { chatNameForMeeting } from "../minutes/chats";
import { useFeature } from "../platform/features";
import { useMeetingSignals } from "../surfaces/meetingSignals";
import { ListenLine, SIGNALS_FEATURE } from "./ListenLine";
import { readListenBrief } from "./listenBrief";
import { marksFor, type ListenBrief } from "./signalMarks";

/** "…" → Edit notes, from the header to the note it edits (detail: `{ meetingId }`). */
export const EDIT_NOTE_EVENT = "vexa:meeting-note-edit";
import {
  notesForView, notesFreshness,
  transcriptLines, updateMeetingNotes, withoutEmptySections, withoutLeadingTitle, leadingTitle,
} from "./meetingNotes";
import { DESK_SLUG } from "../minutes/workspaceReadme";

export const MEETING_VIEW_INSET = 18;

type Notes =
  | { phase: "loading" }
  | { phase: "none" }
  | { phase: "error"; message: string }
  | { phase: "ready"; path: string; workspace: string; body: string; cursor: string; updatedAt: number | null; readonly?: boolean };

/** How often an open meeting view looks at its note again (ms). A write it was not told about — an
 *  Update that ran as a background job, the agent in another chat, the post-meeting flow — shows up
 *  within this, live (founder, 2026-10-14: "it never writes to notes here"). */
export const NOTES_POLL_MS = 4000;

/** Did the note change? Only a change re-renders it: a poll that finds the same file is silent. */
export function sameNote(a: Notes, b: Notes): boolean {
  if (a.phase !== b.phase) return false;
  if (a.phase === "ready" && b.phase === "ready") {
    return a.path === b.path && a.workspace === b.workspace && a.body === b.body && a.cursor === b.cursor && a.updatedAt === b.updatedAt && !!a.readonly === !!b.readonly;
  }
  if (a.phase === "error" && b.phase === "error") return a.message === b.message;
  return true;
}

/** The notes file, read the way every other page on the desk is read — and re-read whenever a turn
 *  commits to the workspace, and every few seconds while the view is on screen, so an Update (or a
 *  change asked for in any chat) shows up without the person reloading anything. A re-read keeps
 *  what is on screen until the new answer arrives, so the notes never flash to a spinner under the
 *  reader, and an unchanged answer changes nothing. */
export function useMeetingNotes(meetingId: string, pollMs: number = NOTES_POLL_MS): Notes & { reload: () => void } {
  const [notes, setNotes] = useState<Notes>({ phase: "loading" });
  const [nonce, setNonce] = useState(0);
  const last = useRef<{ key: string; updatedAt: number | null }>({ key: "", updatedAt: null });
  useEffect(() => {
    let dead = false;
    const keep = (next: Notes) => { if (!dead) setNotes((cur) => (sameNote(cur, next) ? cur : next)); };
    void (async () => {
      const note = await fetchMeetingNote(meetingId);        // never throws; null path = no notes
      if (dead) return;
      if (!note.path) { keep({ phase: "none" }); return; }
      // A REPORT THE SERVER HANDED BACK WHOLE (a switched-off Personal workspace, #1783): shown as
      // it is, read-only — no read route reaches that workspace, and nothing is written there.
      if (note.readonly && typeof note.content === "string") {
        keep({ phase: "ready", path: note.path, workspace: note.workspace, body: note.content, cursor: note.cursor, updatedAt: null, readonly: true });
        return;
      }
      const slug = note.workspace || undefined;
      let body: string | null;
      try { body = await readWorkspaceFile(note.path, slug ? { slug } : undefined); }
      catch { keep({ phase: "error", message: "Could not load the notes. Retry." }); return; }
      if (dead) return;
      // named on the row but not on this desk: there are no notes HERE, and Create puts them here
      if (body === null) { keep({ phase: "none" }); return; }
      // the file's time is read again only when the file changed — a quiet poll costs two reads
      const key = `${note.workspace}|${note.path}|${body}`;
      let updatedAt: number | null = last.current.key === key ? last.current.updatedAt : null;
      if (last.current.key !== key) {
        try {
          const h = await readWorkspaceHistory(note.workspace || DESK_SLUG, { path: note.path, limit: 1 });
          updatedAt = h.commits[0]?.ts || null;
          last.current = { key, updatedAt };
        } catch { /* the time is a nicety; the notes are not withheld for it */ }
      }
      keep({ phase: "ready", path: note.path, workspace: note.workspace, body, cursor: note.cursor, updatedAt });
    })();
    return () => { dead = true; };
  }, [meetingId, nonce]);
  useEffect(() => {
    const onCommit = () => setNonce((n) => n + 1);
    window.addEventListener(WORKSPACE_COMMIT_EVENT, onCommit);
    if (!(pollMs > 0)) return () => window.removeEventListener(WORKSPACE_COMMIT_EVENT, onCommit);
    const t = window.setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      setNonce((n) => n + 1);
    }, pollMs);
    return () => { window.removeEventListener(WORKSPACE_COMMIT_EVENT, onCommit); window.clearInterval(t); };
  }, [pollMs]);
  return { ...notes, reload: () => setNonce((n) => n + 1) };
}

// The look lives in ./meetingView.css (tokens only); geometry that the code computes stays inline.
const sectionHead = "vx-mv-head";
const label = "vx-mv-label";
const quiet = "vx-mv-btn";
const primary = "vx-mv-btn vx-mv-btn-primary";

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
      className="vx-mv-meta vx-mv-busy">
      <span className="vx-op-spin vx-mv-spin" aria-hidden="true" />
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
      className={p.kind === "create" ? primary : quiet}>
      <Icon name={p.kind === "create" ? "plus" : "refresh"} size={13} /> {title}
    </button>
    {failed && <span role="alert" className="vx-mv-meta vx-mv-error">{failed}</span>}
  </>;
}

/** The notes' one control — Update notes, or Create notes when there is no page yet — drawn in the
 *  header's act row (founder, 2026-10-14: "Update notes" appeared twice). A voice note still
 *  recording has none: its notes are written when it stops. */
function NotesControl({ meetingId, notes, lines, liveNote = false }: { meetingId: string; notes: ReturnType<typeof useMeetingNotes>; lines: number; liveNote?: boolean }) {
  if (liveNote || notes.phase === "loading" || notes.phase === "error") return null;
  if (notes.phase === "ready" && notes.readonly) return null;
  if (notes.phase === "ready") {
    const { path, workspace } = notes;
    return <NotesAct kind="update" slot={`notes|${path}`} onFire={async () => updateMeetingNotes(meetingId, path, undefined, workspace || undefined)} />;
  }
  // CREATE IS THE MINT, THEN THE SAME UPDATE. The mint (agent-api, idempotent) puts the page on the
  // desk, the view renders it at once, and when there is
  // anything said yet the same Extend act fills it from the transcript: one press, one writer.
  return <NotesAct kind="create" slot={`notes-create|${meetingId}`} onFire={async () => {
    const page = await createMeetingPage(meetingId);
    notes.reload();
    return lines > 0 ? updateMeetingNotes(meetingId, page.path, undefined, page.workspace || undefined) : null;
  }} />;
}

/** The note's raw file split at its frontmatter: the reader edits the words, the machinery is kept. */
export function splitRaw(raw: string): { fm: string; body: string } {
  if (!raw.startsWith("---")) return { fm: "", body: raw };
  const end = raw.indexOf("\n---", 3);
  if (end === -1) return { fm: "", body: raw };
  const after = raw.indexOf("\n", end + 1);
  const cut = after === -1 ? raw.length : after + 1;
  return { fm: raw.slice(0, cut), body: raw.slice(cut) };
}
const bodyOf = (raw: string) => splitRaw(raw).body;

function NotesSection({ meetingId, notes, liveNote = false }: { meetingId: string; notes: ReturnType<typeof useMeetingNotes>; liveNote?: boolean }) {
  const { segments } = useMeeting().transcript;
  // EDITABLE IN PLACE (founder, UI lab 2026-10-14): the note is the meeting's, written where it is
  // read. Save goes through the same file write every page uses; the frontmatter stays as it was.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const shownNote = notes.phase === "ready" ? withoutEmptySections(withoutLeadingTitle(notesForView(notes.body))) : "";
  const cursorN = notes.phase === "ready" && /^\d+$/.test(notes.cursor.trim()) ? Number(notes.cursor.trim()) : 0;
  const written = shownNote.replace(/<!--[\s\S]*?-->/g, "").replace(/^#+\s.*$/gm, "").trim().length > 0;
  // EDITED LIKE ANY PAGE (founder, 2026-10-15: "should not be different from a page, no need for
  // one-click edit"): "…" → Edit notes in the header, or ask the agent. A click on the note does what
  // a click on a page does — follows a chip or a link — and never opens an editor. The editor shows
  // the words only: the transcript marker and the region fences are put back on save.
  const restore = useRef<(t: string) => string>((t) => t);
  const notesBox = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onEdit = (e: Event) => {
      if ((e as CustomEvent<{ meetingId?: string }>).detail?.meetingId !== meetingId) return;
      if (notes.phase !== "ready") return;
      const e2 = editableNote(notes.body);
      restore.current = e2.restore;
      setDraft(e2.text); setSaveError(""); setEditing(true);
    };
    window.addEventListener(EDIT_NOTE_EVENT, onEdit);
    return () => window.removeEventListener(EDIT_NOTE_EVENT, onEdit);
  }, [meetingId, notes]);
  const save = async () => {
    if (notes.phase !== "ready") return;
    setSaving(true); setSaveError("");
    try {
      // THE FILE THE VIEW READ, WHERE IT READ IT (#1783): the desk is named, never left to the
      // server's default — a default that followed the chat's workspace wrote a copy nobody saw.
      const whole = restore.current(draft);
      // a file with no markers comes back as typed: keep its frontmatter, which the editor never showed
      const content = whole === draft ? splitRaw(notes.body).fm + bodyOf(draft) : whole;
      await writeWorkspaceFile(notes.path, content, { slug: notes.workspace || DESK_SLUG });
      setEditing(false); notes.reload();
    } catch { setSaveError("Could not save. Retry."); }
    finally { setSaving(false); }
  };
  return (
    <section data-meeting-notes aria-labelledby={`notes-${meetingId}`} className="vx-mv-notes">
      <div className={sectionHead}>
        <h2 id={`notes-${meetingId}`} className={label}>Notes</h2>
        {notes.phase === "ready" && <span data-notes-freshness className="vx-mv-meta">
          {notes.readonly
            ? "From your Personal workspace, which is switched off · read only"
            : notesFreshness({ updatedAt: notes.updatedAt, cursor: notes.cursor, segments })}
        </span>}
        <span style={{ flex: "1 1 0%" }} />
        {notes.phase === "ready" && editing && <>
          {saveError && <span role="alert" className="vx-mv-meta vx-mv-error">{saveError}</span>}
          <button type="button" data-notes-cancel className={quiet} disabled={saving} onClick={() => setEditing(false)}>Cancel</button>
          <button type="button" data-notes-save className={primary} disabled={saving} onClick={() => void save()}>{saving ? "Saving…" : "Save"}</button>
        </>}
      </div>
      {/* WHAT VEXA LISTENS FOR (ADR transcript signals): the page's `listen:` brief, one line; people
          change it here and the change is written into the page. Hidden when signals are off. */}
      {notes.phase === "ready" && !editing && <ListenLine raw={notes.body} readonly={!!notes.readonly}
        onSave={async (next) => { await writeWorkspaceFile(notes.path, next, { slug: notes.workspace || DESK_SLUG }); notes.reload(); }} />}
      {notes.phase === "loading" && <div role="status" className="vx-mv-meta">Loading notes…</div>}
      {notes.phase === "error" && <div role="alert" className="vx-mv-meta vx-mv-error">
        {notes.message} <button type="button" className={quiet} onClick={notes.reload}>Retry</button>
      </div>}
      {/* A voice note still recording: its notes come after Stop (the post-meeting flow writes them),
          so the page says so instead of asking for them mid-sentence. */}
      {notes.phase === "none" && liveNote && (
        <div data-notes-empty className="vx-mv-empty">
          <span className="vx-mv-empty-text">Notes are written when you stop recording.</span>
        </div>
      )}
      {notes.phase === "none" && !liveNote && (
        <div data-notes-empty className="vx-mv-empty">
          <span className="vx-mv-empty-text">No notes for this meeting yet — Create notes writes them from the transcript.</span>
        </div>
      )}
      {notes.phase === "ready" && editing && (
        <div data-notes-editor className="vx-mv-notes-editor">
          <MarkdownEditor value={draft} onChange={setDraft} slug={notes.workspace || undefined} />
        </div>
      )}
      {/* A NOTE WITH NOTHING WRITTEN IN IT says so (UI lab, a real call: the page stayed the empty
          template) — never a blank box under the heading. */}
      {notes.phase === "ready" && !editing && !written && (
        <div data-notes-empty className="vx-mv-empty">
          <span className="vx-mv-empty-text">{cursorN > 0
            ? "Nothing to note yet — the last update read the transcript and found nothing to write."
            : "Nothing written in the notes yet — Update notes writes them from the transcript."}</span>
        </div>
      )}
      {notes.phase === "ready" && !editing && written && (
        <div data-notes-body className="vx-mv-notes-body" ref={notesBox} style={{ position: "relative" }}>
          {/* THE PAGE RENDERER (UI decisions 9, 12, 13): the same MdxDoc every page uses — entity chips
              with their hover card, citations, no empty headings — without the outline a long page
              gets, which inside the sidebar is chrome. */}
          <DocMetaContext.Provider value={{ path: notes.path, ...(notes.workspace ? { slug: notes.workspace } : {}) }}>
            <MdxDoc outline={false} properties={false}>{shownNote}</MdxDoc>
          </DocMetaContext.Provider>
          {/* ASK / REFERENCE ON THE NOTES (founder 2026-10-16), like any page */}
          <SelectionAct containerRef={notesBox} act="extend-notes" slot={meetingId}
            onReference={(text, ask) => referenceInChat(text, { meeting: meetingId }, { ask })} />
        </div>
      )}
    </section>
  );
}

/** Where a scroller must sit to put a line in the middle of it — clamped to what it can scroll. */
export function centeredTop(box: { scrollHeight: number; clientHeight: number }, lineTop: number, lineHeight: number): number {
  const want = lineTop - (box.clientHeight - lineHeight) / 2;
  return Math.max(0, Math.min(want, Math.max(0, box.scrollHeight - box.clientHeight)));
}

/** The drawer: one line at the view's foot (collapsed), or grown upward over the note (open). */
export type TranscriptMode = "collapsed" | "open";

/** THE TRANSCRIPT, a different object from the notes (founder, 2026-10-11: *"this should be distinct
 *  it's transcript here - it's not evident from the UX"*): its own surface, its own header (what it
 *  is, how long, whether it is live), conversational rows. In every mode the lines live in ONE
 *  scrolling window, so following works the same everywhere: it sits on the newest line until the
 *  person scrolls up (then *Jump to latest*), and during playback it keeps the played line in view
 *  until the person scrolls away (then *Follow playback*). A scroll this panel made itself is
 *  recognised by where it left the box, so only a person's scroll lets go. */
function TranscriptPanel({ meetingId, mode, onToggle, brief }: { meetingId: string; mode: TranscriptMode; onToggle: () => void; brief?: ListenBrief | null }) {
  const { transcript, meeting } = useMeeting();
  // TRANSCRIPT SIGNALS (ADR transcript signals): chips and emphasis from the meeting's annotations,
  // read with the transcript's own access. None (signals off, nothing scored yet) → the transcript
  // renders exactly as without them.
  const signalsOn = useFeature(SIGNALS_FEATURE);
  const annotations = useMeetingSignals(meeting.platform, meeting.nativeId, signalsOn && !meeting.deleted);
  const marks = useMemo(() => (Object.keys(annotations).length ? marksFor(annotations, brief) : undefined), [annotations, brief]);
  const renderText = useTermRenderer(meetingId);
  const playback = useMeetingPlayback(meeting.id);
  const open = mode === "open";
  const segments = transcript.segments;
  const durableTerminal = ["completed", "failed", "stopped", "past"].includes(meeting.status ?? "");
  const live = meeting.live === true && !durableTerminal;
  // While live the count is what is on screen, the line still being said included.
  const lines = transcriptLines(segments) + (live ? segments.filter((s) => s.completed === false && (s.text ?? "").trim()).length : 0);
  // A RECORDING VOICE NOTE: the transcript is the page's main content, not a four-line window.
  const liveNote = live && meeting.platform === "voice_note";
  const said = segments.filter((x) => (x.text ?? "").trim());
  const latest = said[said.length - 1];
  const box = useRef<HTMLDivElement>(null);       // the lines' own box (selection scope)
  const win = useRef<HTMLDivElement>(null);       // the window that scrolls
  const [copied, setCopied] = useState(false);
  const playing = playback.time !== null;
  const [followLatest, setFollowLatest] = useState(true);
  const [followPlay, setFollowPlay] = useState(true);
  const [above, setAbove] = useState(false);      // lines above the window's top edge
  // SEARCH WITHIN (#1783): only the speaker turns that say it are drawn, the words marked.
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const searching = query.trim().length > 0;
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
    if (!playing && followLatest && !searching) toBottom();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, segments.length, playback.time, followPlay, followLatest, searching]);
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
  return (
    <section data-meeting-transcript data-mode={mode} role="region" aria-labelledby={`${body}-title`} data-open={open ? "" : undefined}
      className="vx-mv-tray">
      {/* ONE ROW (founder, UI lab 2026-10-14: "too much space lost"): what it is on the left, icon
          acts on the right — search opens a field in place, copy, highlight, the chevron. */}
      <div className="vx-mv-tray-row">
        {/* COLLAPSED IT IS THE LATEST LINE, FULL WIDTH (founder, 2026-10-15: "the left part is not
            [useful]"): a live dot while live and "Speaker: text". The word "Transcript", its icon and
            the count live in the opened drawer's header; the word shows collapsed only when nothing
            has been said and nothing is listening. */}
        {open && <span className="vx-mv-tray-icon"><MessageSquareText size={14} aria-hidden /></span>}
        <h2 id={`${body}-title`} className={open ? label : "vx-mv-sr"}>Transcript</h2>
        {open && <span data-transcript-count className="vx-mv-meta vx-mv-count" title={`${lines} ${lines === 1 ? "line" : "lines"}`}>{lines}</span>}
        {live && <span data-transcript-live className="vx-mv-meta vx-mv-live" title="Live">
          <span aria-hidden className="vx-mv-live-dot" />{open ? "live" : <span className="vx-mv-sr">live</span>}</span>}
        {!open && <button type="button" data-transcript-preview className="vx-mv-preview" onClick={onToggle}
          title="Open the transcript">
          {latest
            ? <>{latest.speaker ? <b>{latest.speaker}: </b> : null}{latest.text}</>
            : live ? "Listening…" : "Transcript"}
        </button>}
        {open && <span style={{ flex: "1 1 0%" }} />}
        {open && playing && !followPlay && <button type="button" data-transcript-follow-playback className={quiet}
          onClick={() => { setFollowPlay(true); toActive(); }}>Follow playback</button>}
        {open && !playing && !followLatest && <button type="button" data-transcript-jump className={quiet}
          onClick={() => { setFollowLatest(true); toBottom(); }}>Jump to latest</button>}
        {open && lines > 0 && (searchOpen || query
          ? <span className="vx-mv-search-wrap">
              <input type="search" data-transcript-search aria-label="Search the transcript" placeholder="Search" autoFocus
                value={query} onChange={(e) => { setQuery(e.target.value); const el = win.current; if (el) setTop(el, 0); }}
                onKeyDown={(e) => { if (e.key === "Escape") { setQuery(""); setSearchOpen(false); } }}
                className="vx-mv-search" />
              <button type="button" className="vx-mv-icon" aria-label="Close search" title="Close search"
                onClick={() => { setQuery(""); setSearchOpen(false); }}><X size={13} aria-hidden /></button>
            </span>
          : <button type="button" data-transcript-search-toggle className="vx-mv-icon" aria-label="Search the transcript" title="Search the transcript"
              onClick={() => setSearchOpen(true)}><Search size={14} strokeWidth={1.75} aria-hidden /></button>)}
        {open && <button type="button" className="vx-mv-icon" aria-label="Copy transcript" title={copied ? "Copied" : "Copy transcript"}
          disabled={!lines} onClick={() => void copy()} onBlur={() => setCopied(false)}>
          {copied ? <Check size={14} aria-hidden /> : <Copy size={14} strokeWidth={1.75} aria-hidden />}
        </button>}
        {open && <HighlightButton meeting={meetingId} live={live} icon />}
        <button type="button" data-transcript-toggle aria-expanded={open} aria-controls={body} onClick={onToggle}
          aria-label={open ? "Collapse" : "Show all"} title={open ? "Fold the transcript back to one line" : "Open the transcript over the note"}
          className="vx-mv-icon">
          {open ? <ChevronDown size={15} aria-hidden /> : <ChevronUp size={15} aria-hidden />}
        </button>
      </div>
      <div style={{ position: "relative", minWidth: 0, minHeight: 0, flex: open ? "1 1 0%" : "none", display: open ? "flex" : "none", flexDirection: "column" }}>
        <div id={body} ref={win} data-transcript-window tabIndex={0} aria-label="Transcript lines" onScroll={onScroll}
          hidden={!open} data-live-note={liveNote ? "" : undefined}
          className="vx-mv-window-open"
          style={{ minWidth: 0, flex: "1 1 0%", minHeight: 0, overflowY: "auto", overscrollBehavior: "contain" }}>
          <div ref={box} style={{ position: "relative", minWidth: 0 }}>
            <LiveTranscriptEngine meetingId={meeting.id} segments={segments} gaps={transcript.gaps} renderText={renderText} layout="rows" query={query} marks={marks}
              emptyLabel={meeting.deleted ? "This meeting’s transcript and recording were deleted." : "Nothing said yet — this fills in as the room talks."} />
            <TranscriptExtend containerRef={box} meeting={meetingId} segments={segments} label={(meeting as { title?: string }).title} />
          </div>
        </div>
        {above && <div data-transcript-fade aria-hidden className="vx-mv-fade"
          style={{ position: "absolute", left: 0, right: 0, top: 0, height: 24, pointerEvents: "none" }} />}
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
  const isVoiceNote = meeting.platform === "voice_note";
  const liveNote = isVoiceNote && meeting.live === true && !["completed", "failed", "stopped", "past"].includes(meeting.status ?? "");
  // THE DRAWER'S HEIGHT (founder, UI lab 2026-10-14: "not all-or-nothing — it's resizable"): 0 is
  // the one collapsed line; anything else is how much transcript shows, the note shrinking above it.
  // The person's height is remembered (local, per account); a voice note while it records opens it.
  // BY DEFAULT IT IS OPEN AT 30% of the sidebar (founder, live app.dev: "transcription should be up
  // 30% vertical by default"); the person's own height — or their collapse — is remembered and wins.
  const [height, setHeightRaw] = useState<number | null>(loadDrawerHeight);
  const body = useRef<HTMLDivElement>(null);
  const [bodyH, setBodyH] = useState(0);
  useEffect(() => {
    const el = body.current;
    if (!el) return;
    const read = () => setBodyH(el.clientHeight);
    read();
    const RO = (globalThis as { ResizeObserver?: typeof ResizeObserver }).ResizeObserver;
    if (!RO) return;
    const ro = new RO(read); ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const byDefault = bodyH > 0 ? Math.max(DRAWER.min, Math.round(bodyH * DRAWER.share)) : DRAWER.open;
  const last = useRef(height || byDefault);
  const setHeight = (n: number) => { const v = n <= 0 ? 0 : Math.max(DRAWER.min, Math.round(n)); if (v) last.current = v; setHeightRaw(v); saveDrawerHeight(v); };
  const current = height ?? byDefault;
  const toggle = () => setHeight(current || liveNote ? 0 : (last.current || byDefault));
  const shown = current || (liveNote ? (last.current || byDefault) : 0);
  const mode: TranscriptMode = shown ? "open" : "collapsed";
  const meetings = useLiveMeetings();
  const row = meetings.find((m) => m.id === meetingId || m.native_id === meetingId);
  const speakers = [...new Set(transcript.segments.map((s) => (s.speaker ?? "").trim()).filter(Boolean))];
  const noteBody = notes.phase === "ready" ? notes.body : "";
  const brief = useMemo(() => (noteBody ? readListenBrief(noteBody) : null), [noteBody]);
  // shell2 (UI 7h, 7q): "Chat about this" / "Resume chat" in the meeting's own header
  const shell2 = useFeature("shell2");
  return (
    <div data-meeting-view={meetingId} className="vx-mv-root"
      style={{ position: "relative", height: "100%", flex: "1 1 0%", width: "100%", display: "flex", flexDirection: "column", minHeight: 0, minWidth: 0, "--mv-inset": `${MEETING_VIEW_INSET}px` } as CSSProperties}>
      {/* TOP, FIXED: the meeting's tools and its recording */}
      <header data-meeting-top className="vx-mv-header" style={{ flex: "none" }}>
        <div className="vx-mv-measure">
          <MeetingViewHeader meeting={row} pageTitle={notes.phase === "ready" ? leadingTitle(notesForView(notes.body)) : ""}
            chatName={chatNameForMeeting(meetingId)}
            speakers={speakers}
            notesAct={<NotesControl meetingId={meetingId} notes={notes} lines={transcriptLines(transcript.segments)} liveNote={liveNote} />}
            onEditNotes={notes.phase === "ready" && !notes.readonly && !liveNote ? () => window.dispatchEvent(new CustomEvent(EDIT_NOTE_EVENT, { detail: { meetingId } })) : undefined}>
            {shell2 && <div className="vx-mv-about"><ChatAboutButton a={{ key: `meeting:${meetingId}`, path: `meeting:${meetingId}`, label: chatNameForMeeting(meetingId) || (row ? meetingDisplayName(row) : "the meeting"), kind: "meeting", slug: row?.workspace_id }} /></div>}
            <div className="vx-mv-recorder"><MeetingControls meetingId={meetingId} showBot={false} showDelete={false} /></div>
          </MeetingViewHeader>
        </div>
      </header>
      <MeetingHealthBanner />
      <div ref={body} data-meeting-body style={{ position: "relative", flex: "1 1 0%", minHeight: 0, display: "flex", flexDirection: "column" }}>
        {/* MIDDLE, SCROLLING: the meeting's note */}
        <div data-meeting-main data-centred="" style={{ flex: "1 1 0%", minWidth: 0, minHeight: 0, overflowY: "auto", overflowX: "hidden" }}>
          <div className="vx-mv-content">
            <NotesSection meetingId={meetingId} notes={notes} liveNote={liveNote} />
          </div>
        </div>
        {/* BOTTOM: the transcript drawer — one line, or grown upward over the note */}
        <div data-transcript-drawer data-open={mode === "open" ? "" : undefined} className="vx-mv-drawer"
          style={mode === "open" ? { flex: "0 0 auto", height: shown, maxHeight: "calc(100% - 48px)", display: "flex", flexDirection: "column", minHeight: 0 } : { flex: "0 0 auto" }}>
          <DrawerHandle height={shown} onHeight={setHeight} onToggle={toggle} bodyRef={body} />
          <TranscriptPanel meetingId={meetingId} mode={mode} onToggle={toggle} brief={brief} />
        </div>
      </div>
    </div>
  );
}

/** The drawer's sizes: its share of the sidebar by default, the open height before the sidebar is
 *  measured, the smallest open one, and a keyboard step. */
export const DRAWER = { share: 0.3, open: 320, min: 96, step: 32 } as const;
const HEIGHT_KEY = "vexa.meetingTranscriptHeight";
/** The person's own choice: a height, 0 for collapsed, or null when they have not made one yet. */
function loadDrawerHeight(): number | null {
  try {
    const raw = localStorage.getItem(HEIGHT_KEY);
    if (raw === null) return null;
    const n = Number(raw);
    return Number.isFinite(n) && n >= DRAWER.min ? n : 0;
  } catch { return null; }
}
function saveDrawerHeight(n: number) { try { localStorage.setItem(HEIGHT_KEY, String(n)); } catch { /* storage refused */ } }

/** THE DRAWER'S TOP EDGE: drag (mouse or touch) to set how much transcript shows; it snaps shut
 *  near the bottom and to a third / two thirds of the view; a double-click toggles collapsed and the
 *  last height; arrow keys step it when focused. */
function DrawerHandle({ height, onHeight, onToggle, bodyRef }: {
  height: number; onHeight: (n: number) => void; onToggle: () => void; bodyRef: { current: HTMLDivElement | null };
}) {
  const snap = (n: number) => {
    const total = bodyRef.current?.clientHeight ?? 0;
    if (n < DRAWER.min) return 0;
    if (total > 0) for (const f of [1 / 3, 2 / 3]) if (Math.abs(n - total * f) < 24) return total * f;
    return total > 0 ? Math.min(n, total - 48) : n;
  };
  return <div role="separator" aria-orientation="horizontal" aria-label="Resize the transcript" tabIndex={0} data-transcript-resize
    aria-valuenow={Math.round(height)} className="vx-mv-drawer-handle"
    onDoubleClick={onToggle}
    onKeyDown={(e) => {
      if (e.key === "ArrowUp") { e.preventDefault(); onHeight(height ? height + DRAWER.step : DRAWER.open); }
      if (e.key === "ArrowDown") { e.preventDefault(); onHeight(height - DRAWER.step < DRAWER.min ? 0 : height - DRAWER.step); }
    }}
    onPointerDown={(e) => {
      const y0 = e.clientY, h0 = height;
      let moved = false;
      (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      const move = (ev: PointerEvent) => { moved = true; onHeight(Math.max(0, h0 + (y0 - ev.clientY))); };
      const up = (ev: PointerEvent) => {
        window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
        if (moved) onHeight(snap(Math.max(0, h0 + (y0 - ev.clientY))));
      };
      window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
    }}>
    <span aria-hidden className="vx-mv-drawer-grip" />
  </div>;
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

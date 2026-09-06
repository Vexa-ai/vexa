"use client";
/** Chat — the persistent right-rail agent window. Streams a real agent turn over /api/chat (SSE) into the
 *  turn timeline, surfacing each tool-call as a visible operation (read/search/edit/git/web) with status,
 *  then the message + commit / rejection badge. The composer carries the active center-tab reference. */
import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ClipboardEvent, type DragEvent, type ReactNode } from "react";
import { minutesOnly } from "../app/mode";
import { liveMeetingsNow } from "./liveMeetings";
import { useService, useStore, CommandServiceId } from "../platform";
import { LayoutServiceId, type ActiveTab } from "../workbench/layout";
import { registerCommand, type TabProps } from "../contributions";
import { meetingsOnly } from "../app/mode";
import { AgentWindow, Conversation, opIcon, type Turn, type Op } from "../workbench/agent-window";
import { Icon } from "../ui-kit";
import { ReportTurn } from "./ReportThis";
import { invalidateDocLinkCaches } from "../ui-kit/docLinks";
import { startStreamingDictation, type StreamingDictation } from "../ui-kit/micDictation";
import { sessionTitle, type SessionSummary } from "./sessions";
import { listSessions } from "./sessionsApi";
import { joinInterim, streamChatTurn, type ChatPhase } from "./chatStream";
import { buildChatContext, focusTarget, readIncludeSchedule, scheduleEligible, writeIncludeSchedule, type FocusPayload } from "./chatContext";
import { useLiveMeetings } from "./liveMeetings";
import { meetingPhase, type MeetingMock, type MeetingPhase } from "./meetingModel";
import { presentError } from "./apiClient";
import { promptCarriesActiveContext } from "./surfaceSync";
import type { ChatIntent } from "./chatIntent";
import { surfaceOf, type FrictionSurface } from "./frictionApi";
import { endJob, jobLine, startJob, stepJob, type JobRec } from "./jobs";
import { ARTIFACT_EVENT, ASK_CHAT_EVENT, CHAT_TOUCHED_EVENT, MACHINERY_MARK, WORKSPACE_COMMIT_EVENT, MACHINERY_NOTE, ONBOARDING_KICKOFF_MARK, MINUTES_ONBOARDING_GREETING, MINUTES_PREP_GREETING, ONBOARDING_REPLY_SEP } from "../canvas/actions";
import { TERMS_EVENT } from "../canvas/transcriptTerms";

/** classify a tool name into one of the op icons so the operation line reads at a glance */
function toolOp(tool: string, args?: Record<string, unknown>): Op {
  // `mcp__vexa__entity_upsert` → `entity_upsert`. The namespace is ours, not the reader's, and the
  // founder's own words for what he was watching were "entity_upsert".
  tool = tool.replace(/^mcp__[^_]+(?:_[^_]+)*?__/, "");
  const t = tool.toLowerCase();
  // verb-first labels: the op line reads as what the agent is DOING, not an internal tool name
  const [icon, verb] = /read|cat|open/.test(t) ? [opIcon.read, "Reading"]
    : /glob|search|grep|find|ls\b/.test(t) ? [opIcon.search, "Searching"]
    : /edit|write|append/.test(t) ? [opIcon.edit, "Writing"]
    : /git|commit/.test(t) ? [opIcon.git, "Committing"]
    : /web|fetch|http/.test(t) ? [opIcon.web, "Browsing"]
    : /bash|exec|run/.test(t) ? [opIcon.tool, "Running"]
    : [opIcon.tool, tool];
  // the touched doc, when the tool call names one — powers the turn's actionable file chips
  const file = typeof args?.file_path === "string" ? (args.file_path as string) : undefined;
  const wrote = !!file && /edit|write|append|notebook/.test(t);
  const name = file ? file.split("/").filter(Boolean).pop() : undefined;
  // RUNNING, not done (F66). Every op was appended with `status: "done"`, so the step line rendered
  // a green TICK from the first tool call — an 18-step turn showed a finished-looking line that
  // never moved. Founder: *"i know it's working now, but it just stays like it's stale."* The op
  // that just started IS the one in progress; `onTool` marks the previous one done as it arrives,
  // and the turn's end marks the last one.
  return { icon, label: name ? `${verb} · ${name}` : (verb === tool ? tool : `${verb} · ${tool}`), status: "running", file, wrote };
}

/** Close any step still marked running — the turn is over, so nothing in it is in progress. Errors
 *  keep their own status: a step that FAILED did not succeed because the turn ended. */
function settleOps(ops: Op[]): Op[] {
  return ops.some((o) => o.status === "running")
    ? ops.map((o) => (o.status === "running" ? { ...o, status: "done" as const } : o))
    : ops;
}

/** the backend history turn shape (GET /api/sessions/:session/history).
 *
 *  `text` is what the MODEL was given: the worker's preambles, the control plane's grounding, and
 *  the person's sentence at the end of it. `user_text` is what the PERSON typed — recorded as its
 *  own field by the worker (`worker/engine.py` record_user_text) and served verbatim by
 *  `workspace_reader.history`. It is optional because records written before the field existed do
 *  not have it, and only because of that: for every turn taken from now on it is the only thing
 *  this surface should render. */
type HistoryTurn =
  | { role: "user"; text: string; user_text?: string }
  | { role: "agent"; text: string; ops?: { label: string; file?: string; wrote?: boolean }[]; commit?: string };

type AgentTurn = Extract<Turn, { role: "agent" }>;
type ChatSessionState = {
  turns: Turn[];
  busy: boolean;
  loading: boolean;
  loaded: boolean;
  nextId: number;
  abort: AbortController | null;
  /** BACKGROUND JOBS still running for this thread (Vexa-ai/vexa#1584). Deliberately NOT `busy`:
   *  the whole point of a job is that the chat is answerable while it runs. Several at once is the
   *  normal case, so it is a list. */
  jobs: JobRec[];
};

const EMPTY_CHAT_STATE: ChatSessionState = { turns: [], busy: false, loading: false, loaded: false, nextId: 0, abort: null, jobs: [] };
const chatSessions = new Map<string, ChatSessionState>();
const chatSubscribers = new Map<string, Set<() => void>>();

function chatStateKey(subject: string, session: string): string {
  return `${subject}\u0000${session}`;
}

function getChatState(key: string): ChatSessionState {
  let state = chatSessions.get(key);
  if (!state) {
    state = { ...EMPTY_CHAT_STATE };
    chatSessions.set(key, state);
  }
  return state;
}

function emitChatState(key: string): void {
  chatSubscribers.get(key)?.forEach((fn) => fn());
}

function updateChatState(key: string, fn: (state: ChatSessionState) => ChatSessionState): void {
  chatSessions.set(key, fn(getChatState(key)));
  emitChatState(key);
}

function subscribeChatState(key: string, cb: () => void): () => void {
  let subs = chatSubscribers.get(key);
  if (!subs) {
    subs = new Set();
    chatSubscribers.set(key, subs);
  }
  subs.add(cb);
  return () => {
    subs?.delete(cb);
    if (subs?.size === 0) chatSubscribers.delete(key);
  };
}

function patchAgentTurn(key: string, agentId: string, fn: (turn: AgentTurn) => AgentTurn): void {
  updateChatState(key, (state) => ({
    ...state,
    turns: state.turns.map((turn) => (turn.id === agentId && turn.role === "agent" ? fn(turn) : turn)),
  }));
}

/** map a backend op label (read/search/edit/git/web/tool) to a frontend Op (icon from opIcon) */
const OP_VERB: Record<string, string> = { read: "Reading", search: "Searching", edit: "Writing", git: "Committing", web: "Browsing", tool: "Working" };
function historyOp(op: { label: string; file?: string; wrote?: boolean }): Op {
  const name = op.file ? op.file.split("/").filter(Boolean).pop() : undefined;
  const verb = OP_VERB[op.label] ?? op.label;
  return { icon: opIcon[op.label] ?? opIcon.tool, label: name ? `${verb} · ${name}` : verb, status: "done", file: op.file, wrote: op.wrote };
}

type ReferenceToken = { kind: "file" | "meeting"; value: string; raw: string };
type ReferenceSegment = { kind: "text"; text: string } | { kind: "reference"; ref: ReferenceToken };
type ActiveReference = ReferenceToken;
const REFERENCE_RE = /@(file|meeting):([A-Za-z0-9._~%+@:/=-]+)/g;
const MAX_TEXTAREA_HEIGHT = 156;
const ATTACHMENT_ACCEPT = [
  "image/*", ".pdf", ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".log",
  ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip",
].join(",");

type ComposerAttachment = { id: string; file: File; isImage: boolean; previewUrl?: string };
type UploadedWorkspaceFile = { name: string; path: string };

function resizeComposerTextarea(el: HTMLTextAreaElement) {
  el.style.height = "auto";
  const height = Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT);
  el.style.height = `${height}px`;
  el.style.overflowY = el.scrollHeight > MAX_TEXTAREA_HEIGHT ? "auto" : "hidden";
}

function attachmentPrompt(prompt: string, files: UploadedWorkspaceFile[]): string {
  if (files.length === 0) return prompt.trim();
  const attached = ["Attached files:", ...files.map((f) => `- @file:${f.path}`)].join("\n");
  return prompt.trim() ? `${prompt.trim()}\n\n${attached}` : attached;
}

function tokenizeReferences(text: string): ReferenceSegment[] {
  const parts: ReferenceSegment[] = [];
  REFERENCE_RE.lastIndex = 0;
  let last = 0;
  for (const m of text.matchAll(REFERENCE_RE)) {
    const index = m.index ?? 0;
    if (index > last) parts.push({ kind: "text", text: text.slice(last, index) });
    parts.push({ kind: "reference", ref: { kind: m[1] as "file" | "meeting", value: m[2], raw: m[0] } });
    last = index + m[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

function referenceTokens(text: string): ReferenceToken[] {
  const out: ReferenceToken[] = [];
  const seen = new Set<string>();
  for (const part of tokenizeReferences(text)) {
    if (part.kind !== "reference") continue;
    const key = `${part.ref.kind}:${part.ref.value}`;
    if (!seen.has(key)) { seen.add(key); out.push(part.ref); }
  }
  return out;
}

function fileLabel(path: string): string {
  return path.split("/").filter(Boolean).pop()?.replace(/\.md$/, "") || path;
}

function ReferenceChip({ refToken }: { refToken: ReferenceToken }) {
  const isFile = refToken.kind === "file";
  const label = isFile ? fileLabel(refToken.value) : refToken.value;
  return (
    <span title={refToken.raw}
      style={{ display: "inline-flex", alignItems: "center", gap: 5, maxWidth: 220, verticalAlign: "baseline", margin: "0 2px", padding: "1px 7px 1px 5px", borderRadius: 6, border: "1px solid var(--line2)", background: isFile ? "var(--bluebg)" : "var(--accentbg)", color: isFile ? "var(--blue)" : "var(--accent)", fontSize: "0.92em", lineHeight: 1.45, whiteSpace: "nowrap" }}>
      <Icon name={isFile ? "file" : "cal"} size={11} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span>
    </span>
  );
}

function ReferenceText({ text }: { text: string }) {
  return <>{tokenizeReferences(text).map((part, i) => part.kind === "text"
    ? <span key={i}>{part.text}</span>
    : <ReferenceChip key={i} refToken={part.ref} />)}</>;
}

function appendReferenceToken(text: string, refToken: ReferenceToken | null): string {
  const body = text.trim();
  if (!refToken || body.includes(refToken.raw)) return body;
  return body ? `${body}\n\n${refToken.raw}` : refToken.raw;
}

function meetingTokenFromTitle(title: string): ReferenceToken {
  const value = (title.split("·").pop()?.trim() || title.trim() || "meeting").replace(/^["'\\]+|["'\\.)]+$/g, "");
  return { kind: "meeting", value, raw: `@meeting:${value}` };
}

// Server-side grounding preambles (kg-links, mount stack, meeting phase, schedule digest,
// workspace focus) are PART of the stored prompt — plumbing, not something the user typed.
// Each known block is stripped start→terminator so history shows only the user's words
// (fail-soft: an unrecognized shape renders untouched). Mirrors worker/engine.py + meeting_steering.py.
const CONTEXT_BLOCKS: Array<[RegExp, RegExp]> = [
  [/^## Referencing knowledge \(always\)/, /or use plain text\.\s*/],
  [/^## Your mounted workspaces/, /do not guess or invent mount paths\.\s*/],
  [/^<schedule[ >]/, /<\/schedule>\s*/],   // the digest opens with attributes (tz/now) — match them
  [/^The user's meeting schedule is in <schedule>/, /notes files or ask\.\s*/],
  [/^You are assisting in a live meeting/, /(?:<\/transcript>\s*|no transcript yet\.\s*)/],
  // the prep steering grew (proactive-research + example-entities + identity clauses); anchor on its
  // CURRENT tail. ("don't have prior context." now appears mid-block, so it can't be the terminator.)
  [/^You are helping the user PREPARE/, /starting blank\.\s*/],
  [/^The meeting "/, /(?:<\/transcript>\s*|invent its content\.\s*)/],
  [/^The user is looking at the workspace/, /(?:<\/readme>\s*|context is missing\.\s*)/],
];

// The grounding→user boundary marker (control_plane/api.py CONTEXT_SENTINEL, and the composer below
// writes it too). When present, ONE cut removes every folded block regardless of wording drift.
const CONTEXT_SENTINEL = "<!--vexa:user-input-below-->";

/** THIS IS THE FALLBACK, AND IT IS ONLY THE FALLBACK (F47).
 *
 *  Every turn taken from 2026-09-02 onward carries the person's words as their own field
 *  (`HistoryTurn.user_text`), and the loader below renders that field and never a composed prompt.
 *  This function reconstructs the human half of an OLDER record by stripping the machinery off the
 *  front — the sentinel when the record has one, else the wording-matched blocks above.
 *
 *  It is kept, and it is not to be relied on again. Reconstruction by stripping is derived from text
 *  the SERVER owns and nothing checks the derivation, so it fails silently and completely whenever a
 *  preamble changes shape: on 2026-09-02 the preamble set changed and every turn in the founder's
 *  chat rendered as a grey USER bubble containing "## Referencing knowledge (always)", the mount
 *  stack and the write-routing policy, with his own sentence buried at the bottom. Do not extend it
 *  to cover a new preamble; the field is the answer. */
export function stripContextBlocks(raw: string): string {
  const si = raw.lastIndexOf(CONTEXT_SENTINEL);
  if (si >= 0) {
    const after = raw.slice(si + CONTEXT_SENTINEL.length).trimStart();
    if (after) return after;   // everything up to & including the sentinel is server grounding
  }
  let text = raw;
  let guard = 0;
  outer: while (guard++ < 12) {
    for (const [start, end] of CONTEXT_BLOCKS) {
      if (start.test(text)) {
        const m = end.exec(text);
        if (m) { text = text.slice(m.index + m[0].length).trimStart(); continue outer; }
      }
    }
    break;
  }
  return text === raw ? raw : (text.trim() || raw);
}

function compactStoredUserText(text: string): string {
  const raw = stripContextBlocks(text.trim());
  // An onboarding first reply is stored as `<grounding>[reply]<user text>` — show only the user's text.
  if (raw.includes(ONBOARDING_KICKOFF_MARK)) {
    const i = raw.indexOf(ONBOARDING_REPLY_SEP);
    if (i >= 0) return raw.slice(i + ONBOARDING_REPLY_SEP.length).trim();
  }
  const legacyCopilot = raw.match(/^You are the copilot for a live meeting \("([^"]+)"\)\. The meeting transcript so far:[\s\S]*?\n?---\s*([\s\S]*)$/);
  if (legacyCopilot) {
    return appendReferenceToken(legacyCopilot[2], meetingTokenFromTitle(legacyCopilot[1]));
  }
  const activeMeeting = raw.match(/^Active meeting reference:\s*(@meeting:([A-Za-z0-9._~%+@:/=-]+))[\s\S]*?\n\n---\n([\s\S]*)$/);
  if (activeMeeting) {
    return appendReferenceToken(activeMeeting[3], { kind: "meeting", value: activeMeeting[2], raw: activeMeeting[1] });
  }
  const legacyMeeting = raw.match(/^Active meeting ([A-Za-z0-9._~%+@:/=-]+)\.[\s\S]*?\n\n---\n([\s\S]*)$/);
  if (legacyMeeting) {
    return appendReferenceToken(legacyMeeting[2], { kind: "meeting", value: legacyMeeting[1], raw: `@meeting:${legacyMeeting[1]}` });
  }
  const activeFile = raw.match(/^Active context: the user is viewing the workspace file ([^\n]+?)\. Read it[\s\S]*?\n\n---\n([\s\S]*)$/);
  if (activeFile) {
    return appendReferenceToken(activeFile[2], { kind: "file", value: activeFile[1], raw: `@file:${activeFile[1]}` });
  }
  // The default is the CONTEXT-STRIPPED text (sentinel/regex) — NOT the raw stored prompt. Returning
  // `text` here silently discarded the strip, leaking the whole grounding preamble into the bubble.
  return raw;
}

/** WHAT A STORED USER TURN RENDERS AS — the whole rule, in one place (F47).
 *
 *  `user_text` is the person's own words, recorded as their own field by the worker at the moment
 *  the turn was composed. When it is there it is the answer, verbatim: the composed prompt is never
 *  shown, whatever preambles happen to be in front of it this month.
 *
 *  Only a record written before that field existed falls through to `compactStoredUserText`, which
 *  reconstructs the human half by stripping the machinery off the front — the sentinel if the record
 *  carries one, else the wording-matched preamble blocks. That path is the fallback and nothing
 *  else; see `stripContextBlocks` for why it must never be the primary again. */
export function historyUserText(t: { text: string; user_text?: string }): string {
  return t.user_text ?? compactStoredUserText(t.text);
}

const userBubble: CSSProperties = { maxWidth: "82%", margin: "0 0 0 auto", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 12, borderTopRightRadius: 4, padding: "8px 12px", fontSize: 13, color: "var(--t1)", lineHeight: 1.5, whiteSpace: "pre-wrap" };

function ChatHeader({ subject, session, onSelectSession, onNewChat, onClose }: {
  subject: string;
  session: string;
  onSelectSession: (id: string) => void;
  onNewChat: () => void;
  onClose: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const list = await listSessions();
        if (!cancelled) { setSessions(list); setError(null); }
      } catch (e) {
        // Fail loud: surface the backend error instead of silently showing an empty list.
        if (!cancelled) setError(e instanceof Error ? e.message : "Couldn't load sessions");
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [subject]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const load = async () => {
      try {
        const list = await listSessions();
        if (!cancelled) { setSessions(list); setError(null); }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Couldn't load sessions");
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [open, subject]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (menuRef.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); setOpen(false); }  // consume: close-topmost beats nav.back
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const activeSummary = sessions.find((s) => s.session === session) ?? { session };
  const visibleSessions = sessions.some((s) => s.session === session) ? sessions : [activeSummary, ...sessions];
  const currentTitle = sessionTitle(activeSummary);
  const iconButton: CSSProperties = { width: 28, height: 28, borderRadius: 7, border: "1px solid transparent", background: "transparent", color: "var(--t3)", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", flex: "none" };

  if (minutesOnly()) return null;  // MINUTES: the rail owns chats — no second header inside the panel
  return (
    <div ref={menuRef} style={{ height: 38, flex: "none", position: "relative", display: "flex", alignItems: "center", gap: 4, padding: "0 8px", borderBottom: "1px solid var(--line)", background: "var(--panel)", minWidth: 0 }}>
      <button
        aria-label="Switch chat session"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{ flex: 1, minWidth: 0, height: 28, borderRadius: 7, border: "1px solid transparent", background: open ? "var(--panel2)" : "transparent", color: "var(--t1)", display: "flex", alignItems: "center", gap: 7, padding: "0 8px", cursor: "pointer" }}
      >
        <Icon name="msg" size={13} style={{ color: "var(--t3)" }} />
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12.5, lineHeight: 1 }}>{currentTitle}</span>
        <Icon name="chevR" size={12} style={{ color: "var(--t3)", transform: open ? "rotate(-90deg)" : "rotate(90deg)", transition: "transform .12s" }} />
      </button>
      <button aria-label="New chat" title="New chat" onClick={onNewChat} style={iconButton}><Icon name="plus" size={15} /></button>
      <button aria-label="Close chat" title="Close chat" onClick={onClose} style={iconButton}><Icon name="x" size={14} /></button>

      {open && (
        <div role="menu" style={{ position: "absolute", zIndex: 30, top: 36, left: 8, right: 8, maxHeight: 260, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 8, background: "var(--panel)", boxShadow: "0 14px 34px rgba(0,0,0,.32)", padding: 4 }}>
          {error && <div role="alert" style={{ padding: "8px", color: "var(--danger)", fontSize: 12 }}>⚠ Couldn&apos;t load sessions — {error}</div>}
          {visibleSessions.map((s) => {
            const active = s.session === session;
            return (
              <button
                key={s.session}
                role="menuitemradio"
                aria-checked={active}
                onClick={() => { onSelectSession(s.session); setOpen(false); }}
                style={{ width: "100%", minWidth: 0, display: "flex", alignItems: "center", gap: 8, padding: "7px 8px", border: "none", borderRadius: 6, background: active ? "var(--panel2)" : "transparent", color: active ? "var(--t1)" : "var(--t2)", cursor: "pointer", textAlign: "left", fontSize: 12.5 }}
              >
                <Icon name="msg" size={13} style={{ color: active ? "var(--t2)" : "var(--t3)" }} />
                <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sessionTitle(s)}</span>
              </button>
            );
          })}
          {visibleSessions.length === 0 && <div style={{ padding: "8px", color: "var(--t3)", fontSize: 12 }}>No recent sessions</div>}
        </div>
      )}
    </div>
  );
}

function ChatConversation({ turns, busy, empty, surface }: { turns: Turn[]; busy?: boolean; empty?: ReactNode; surface?: FrictionSurface }) {
  if (turns.length === 0 && empty) return <>{empty}</>;
  return (
    <>
      {turns.map((t, i) => t.role === "user"
        ? <div key={t.id} style={{ marginBottom: 16 }}><div style={userBubble}><ReferenceText text={t.text} /></div></div>
        // "REPORT THIS" ON A TURN (PRD decision 33 §2). Only the AGENT's turns carry it: the person
        // reporting their own sentence is not a rough edge, and an action on every bubble is twice
        // the chrome for half the meaning. The surface travels with the report — chat, kind, the open
        // page — so nobody is asked to describe where they were.
        : <ReportTurn key={t.id} surface={{ ...(surface ?? {}), at: "turn", quote: t.text }}>
            <Conversation turns={[t]} busy={!!busy && i === turns.length - 1} />
          </ReportTurn>)}
    </>
  );
}

function ComposerReferences({ text }: { text: string }) {
  const refs = referenceTokens(text);
  if (refs.length === 0) return null;
  return (
    <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 5, minWidth: 0 }}>
      {refs.map((r) => <ReferenceChip key={`${r.kind}:${r.value}`} refToken={r} />)}
    </div>
  );
}

function AttachmentChips({ attachments, onRemove }: { attachments: ComposerAttachment[]; onRemove: (id: string) => void }) {
  if (attachments.length === 0) return null;
  return (
    <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 6, minWidth: 0 }}>
      {attachments.map((a) => (
        <span key={a.id} title={a.file.name}
          style={{ display: "inline-flex", alignItems: "center", gap: 6, maxWidth: 210, minWidth: 0, border: "1px solid var(--line2)", borderRadius: 7, background: "var(--panel2)", color: "var(--t2)", padding: "3px 5px", fontSize: 12, lineHeight: 1.2 }}>
          {a.previewUrl
            ? <img src={a.previewUrl} alt="" style={{ width: 24, height: 24, borderRadius: 4, objectFit: "cover", flex: "none", background: "var(--bg)" }} />
            : <span style={{ width: 24, height: 24, borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center", flex: "none", background: "var(--bg)", color: "var(--t3)" }}><Icon name="file" size={13} /></span>}
          <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.file.name || "upload"}</span>
          <button aria-label={`Remove ${a.file.name || "attachment"}`} title="Remove" type="button" onClick={() => onRemove(a.id)}
            style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", padding: 1, flex: "none" }}>
            <Icon name="x" size={12} />
          </button>
        </span>
      ))}
    </div>
  );
}

function referenceContext(text: string): string {
  const refs = referenceTokens(text);
  if (refs.length === 0) return "";
  const lines = [
    "Referenced context:",
    "The user included these paste-safe reference tokens. Resolve them before answering when relevant.",
  ];
  for (const ref of refs) {
    if (ref.kind === "file") {
      lines.push(
        `- token: ${ref.raw}`,
        "  kind: file",
        `  workspace_path: ${ref.value}`,
        "  instruction: Read this workspace-relative path before relying on it.",
      );
    } else {
      const notesPath = `kg/entities/meeting/${ref.value}.md`;
      lines.push(
        `- token: ${ref.raw}`,
        "  kind: meeting",
        `  native_id: ${ref.value}`,
        "  platform: google_meet",
        `  notes_workspace_path: ${notesPath}`,
        `  transcript_api_path: /api/transcripts/google_meet/${ref.value}`,
        "  instruction: Use notes_workspace_path first; fetch or identify the transcript only when needed. Keep the visible chat compact: refer to the token instead of pasting the transcript.",
      );
    }
  }
  return lines.join("\n");
}

function promptWithReferences(prompt: string, userText: string): string {
  const context = referenceContext(userText);
  // THE PERSON'S WORDS COME LAST, AND THE SENTINEL SAYS WHERE THEY START (F47). The reference block
  // is machinery this composer wrote — token resolution instructions for the model — and it used to
  // be appended AFTER the sentence, which put it inside everything downstream treats as "what the
  // person said": the worker records that half as `user_text`, and the bubble would have shown a
  // `- token: @meeting:…  kind: meeting  native_id: …` dump under every question about a meeting.
  // Moving it in front and marking the boundary costs the model nothing (grounding first, ask last,
  // like every other preamble) and makes the human half exact rather than approximately right. The
  // server inserts its own sentinel in front of this whole string; both readers take the LAST one.
  return context ? `${context}\n\n---\n${CONTEXT_SENTINEL}${prompt.trim()}` : prompt.trim();
}

function activeReference(tab: ActiveTab | null): ActiveReference | null {
  if (!tab) return null;
  const path = typeof tab.params.path === "string" ? tab.params.path : null;
  if ((tab.kind === "doc" || tab.kind === "file") && path) return { kind: "file", value: path, raw: `@file:${path}` };
  const meetingId = typeof tab.params.meetingId === "string" ? tab.params.meetingId : null;
  // A PREP tab focuses its meeting too — the chat enters "Preparing" mode for it (W3/W4).
  if ((tab.kind === "meeting" || tab.kind === "meetingPrep") && meetingId) return { kind: "meeting", value: meetingId, raw: `@meeting:${meetingId}` };
  return null;
}

// ── chat MODE (design-spec meeting-lifecycle-v2, W3): the composer states its meeting phase ────────
const MODE_CHIP: Record<MeetingPhase, { label: string; color: string; bg: string }> = {
  prep: { label: "Preparing", color: "var(--accent)", bg: "var(--accentbg)" },
  live: { label: "In meeting", color: "var(--green)", bg: "var(--greenbg)" },
  post: { label: "Recap", color: "var(--violet)", bg: "var(--violetbg)" },
};
const MODE_PLACEHOLDER: Record<MeetingPhase, string> = {
  prep: "Ask me to build the agenda, research attendees, or draft the brief…",
  live: "Ask about what's being said…",
  post: "Ask for the recap, decisions, or follow-up drafts…",
};
const meetingLabel = (m: MeetingMock) => m.title_custom ?? (m.native_id ?? m.title).replace(/^Google Meet · /, "");

function activeContextPrompt(ref: ActiveReference | null, meeting: MeetingMock | undefined): string {
  if (!ref) return "";
  if (ref.kind === "file") {
    // PRD decision 30: this narration and the server's surface record are two answers to one
    // question. While the record is not live the prompt keeps carrying it — dropping it first would
    // leave the agent knowing LESS than it does today. One flag flips both halves together.
    if (!promptCarriesActiveContext()) return "";
    return `Active context: the user is viewing the workspace file ${ref.value}. Read it with your Read tool if relevant.`;
  }

  // Meeting grounding now happens SERVER-SIDE: agent-api folds the live transcript from the meeting's
  // redis stream into the prompt (see _meeting_grounding). The client only flags the active meeting via
  // `active` on the POST body — no prompt preamble, so we never point the agent at a notes file.
  return "";
}

/** The meetings platform as the api slug agent-api keys the transcript stream on. */
function meetingPlatformSlug(meeting: MeetingMock | undefined): string {
  const p = meeting?.platform;
  return p === "Google Meet" || p === "google_meet" ? "google_meet" : (p ?? "google_meet");
}

function promptWithActiveContext(prompt: string, ref: ActiveReference | null, meeting: MeetingMock | undefined): string {
  const context = activeContextPrompt(ref, meeting);
  return context ? `${context}\n\n---\n${prompt.trim()}` : prompt.trim();
}

const ROUTINE_COMMAND = "/routine";
const ROUTINE_NAME_STOP_WORDS = new Set([
  "a", "an", "and", "as", "at", "by", "create", "each", "every", "for", "from", "in", "into",
  "me", "my", "of", "on", "our", "please", "routine", "scheduled", "the", "to", "with",
  "hour", "hours", "day", "days", "week", "weeks", "month", "months", "am", "pm",
]);

function isRoutineCommand(text: string): boolean {
  return /^\/routine(?:\s|$)/i.test(text);
}

function routineDescription(text: string): string {
  return text.replace(/^\/routine(?:\s+|$)/i, "").trim();
}

function routineFileStem(description: string): string {
  const words = description.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  const stem = words
    .filter((word) => !ROUTINE_NAME_STOP_WORDS.has(word) && !/^\d+(?:am|pm)?$/.test(word))
    .slice(0, 6)
    .join("-");
  return stem || "scheduled-routine";
}

function routineCreationPrompt(commandText: string): string {
  const description = routineDescription(commandText);
  if (!description) {
    return [
      "The user invoked /routine without a routine description.",
      "Ask one concise follow-up for the task to run and the cadence. Do not create a routine file until the user gives enough detail, or explicitly accepts a default daily 9 AM schedule.",
    ].join("\n\n");
  }

  const fileStem = routineFileStem(description);
  return [
    `Create a scheduled routine from this user request: ${JSON.stringify(description)}.`,
    "",
    "You must write the routine into the user's workspace as a markdown file. Do not only explain the routine.",
    `Use this path unless a clearly better concise kebab-case name fits the request: routines/${fileStem}.md`,
    "",
    "The file must have YAML frontmatter in exactly this shape:",
    "---",
    "enabled: true",
    'cron: "<valid 5-field cron expression>"',
    "prompt: |",
    "  <the task prompt the scheduled agent should run>",
    "---",
    "",
    "Derive the cron from the user's schedule words. Examples: \"every 2 hours\" => \"0 */2 * * *\"; \"at 9am\" => \"0 9 * * *\". If no schedule is explicit, use daily at 9 AM local scheduler time: \"0 9 * * *\".",
    "Make the prompt the actual recurring task, with schedule wording removed unless it is necessary context.",
    "After writing the file, briefly confirm the path and cron.",
  ].join("\n");
}

/** `emptyExtra` — whatever the host wants in the void an empty conversation leaves between its
 *  greeting and the composer. The chat owns that layout and nothing else: it never decides what
 *  goes there (the minutes shell derives its proposal chips), so the two stay independent. */
type ChatProps = Partial<TabProps> & { emptyExtra?: ReactNode };


/** WHICH greeting is TRUE in this room — and in every room but one, the answer is NONE.
 *
 *  ── F36, founder ruling 2026-09-02 ────────────────────────────────────────────────────────────
 *  A chat opened with `+` shows an EMPTY COMPOSER AND NOTHING ELSE. Two lines are deleted here,
 *  not made unreachable:
 *    · "A fresh thread in this project. Ask across everything its workspaces hold …" — the
 *      empty-state of a plain chat, which he met in a chat he had never created;
 *    · "👋 I'm your agent here … paste a meeting link …" — the home greeting, which greeted a chat
 *      that was about nothing.
 *  Both were DEFAULTS: nothing in anyone's state produced them, they filled a blank page. His words
 *  on finding them: *"i do not like this text."*
 *
 *  What survives is the pair a MEETING produces, because the room is about something and the line
 *  is true of that thing. Minutes language is only honest in a chat BOUND TO A MEETING: the old
 *  rule asked "does this account have any held meeting?" and answered it for every session, so the
 *  home chat greeted a brand-new account with "I kept the minutes of your meeting" (founder,
 *  2026-09-01, on an account with no meetings at all). A `meet-` room asks about ITS OWN meeting;
 *  everywhere else says nothing at all. */
function minutesEmptyGreeting(session: string): string {
  if (!session.startsWith("meet-")) return "";
  const held = liveMeetingsNow().some((mm) => session === `meet-${mm.id}` && ["completed", "stopped", "failed"].includes(String((mm as { live_status?: string }).live_status)));
  return (held ? MINUTES_ONBOARDING_GREETING : MINUTES_PREP_GREETING).replace("👋 ", "").replace(/\*\*/g, "");
}

export function Chat({ params = {}, emptyExtra }: ChatProps) {
  const subject = typeof params.subject === "string" ? params.subject : "me";  // LOCAL chat-cache key only — never sent upstream; scope is server-derived from the authed user (P20)
  const commands = useService(CommandServiceId);
  const layout = useService(LayoutServiceId);
  const { activeTab, activeSession, activeList } = useStore(layout.store);
  // the rail follows the store's active session (switched from the rail header or Sessions list); params override if ever passed.
  const session = typeof params.session === "string" && params.session.trim() ? params.session : activeSession;
  const chatKey = chatStateKey(subject, session);
  const chatState = useSyncExternalStore(
    (cb) => subscribeChatState(chatKey, cb),
    () => getChatState(chatKey),
    () => getChatState(chatKey),
  );
  const { turns, busy, loading } = chatState;
  const activeRef = activeReference(activeTab);
  // the user can clear focus with the chip's ×; a newly-focused tab re-shows it.
  const [focusCleared, setFocusCleared] = useState(false);
  useEffect(() => { setFocusCleared(false); }, [activeRef?.raw]);
  // ambient schedule digest (context bundle): surface-gated, with a per-session explicit toggle
  const ambientEligible = scheduleEligible(activeList, activeTab);
  const [includeSchedule, setIncludeSchedule] = useState<boolean | null>(null);
  useEffect(() => { setIncludeSchedule(readIncludeSchedule(session)); }, [session]);
  const setAmbient = (v: boolean | null) => { setIncludeSchedule(v); writeIncludeSchedule(session, v); };
  const ambientOn = includeSchedule !== null ? includeSchedule : ambientEligible;
  // the bundle focus payload — meeting/file mirror the legacy `active`; workspace/today are new kinds
  const bundleFocus: FocusPayload | null = focusCleared ? null : focusTarget(activeTab);
  const focusRef = focusCleared ? null : activeRef;
  const meetings = useLiveMeetings();
  const activeMeeting = activeRef?.kind === "meeting"
    ? meetings.find((m) => m.id === activeRef.value || m.native_id === activeRef.value)
    : undefined;
  // MINUTES: the composer stops ADVERTISING the focused doc (founder, 2026-09-01 — same treatment
  // as 722629588 gave the schedule chip). The context still flows: contextRef below continues to
  // ground the prompt and to fill the wire bundle; only the FOCUS chip and the badge stamped on the
  // user bubble go away. In minutes mode the open document is already visible in the panel beside
  // the conversation, so the badge was repeating what the eye can see.
  const advertiseFocus = !minutesOnly();
  const contextRef: ActiveReference | null = focusRef?.kind === "meeting"
    ? { kind: "meeting", value: activeMeeting?.native_id ?? activeMeeting?.id ?? focusRef.value, raw: `@meeting:${activeMeeting?.native_id ?? activeMeeting?.id ?? focusRef.value}` }
    : focusRef;
  const [uploading, setUploading] = useState(false);
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Follow the stream ONLY while the user is pinned to the bottom. Scrolling up to read
  // detaches (streaming updates no longer yank the view); scrolling back down re-attaches.
  // Sending a message always re-attaches — that's a human action asking for the reply.
  const stickToBottomRef = useRef(true);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => { stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const attachmentSeqRef = useRef(0);
  const attachmentsRef = useRef<ComposerAttachment[]>([]);
  // ── mic dictation — STREAMING, meeting-pipeline style (sliding window + LocalAgreement
  //    via ui-kit/micDictation): confirmed + pending text land in the composer LIVE while
  //    speaking; stop flushes the final window. STT is proxied via /api/stt.
  const [mic, setMic] = useState<"idle" | "rec" | "stt">("idle");
  const [micError, setMicError] = useState<string | null>(null);
  const micRef = useRef<StreamingDictation | null>(null);
  const micBaseRef = useRef("");     // composer text at record start — dictation appends after it
  const micStartRef = useRef(0);
  useEffect(() => () => { micRef.current?.cancel(); }, []);  // release the mic on unmount
  const micCompose = (base: string, confirmed: string, pending: string) => {
    const dictated = pending ? (confirmed ? `${confirmed} ${pending}` : pending) : confirmed;
    return base ? (dictated ? `${base} ${dictated}` : base) : dictated;
  };
  const toggleMic = async () => {
    if (mic === "stt") return;
    if (mic === "rec") {
      const d = micRef.current;
      micRef.current = null;
      if (!d) { setMic("idle"); return; }
      if (Date.now() - micStartRef.current < 300) { d.cancel(); setMic("idle"); return; }  // accidental tap
      setMic("stt");
      try {
        const final = await d.stop();
        setValue(micCompose(micBaseRef.current, final, ""));
        window.setTimeout(() => inputRef.current?.focus(), 0);
      } catch (e) {
        setMicError(e instanceof Error ? e.message : "Transcription failed");
      } finally { setMic("idle"); }
      return;
    }
    try {
      setMicError(null);
      micBaseRef.current = value.trim();
      micStartRef.current = Date.now();
      micRef.current = await startStreamingDictation({
        onUpdate: (confirmed, pending) => setValue(micCompose(micBaseRef.current, confirmed, pending)),
        onError: () => { /* transient mid-stream faults retry on the next window — stay quiet */ },
      });
      setMic("rec");
    } catch { setMicError("Microphone unavailable — check browser permissions."); }
  };

  useEffect(() => {
    const focus = () => inputRef.current?.focus();
    window.addEventListener("vexa:terminal:focus-chat", focus);
    return () => window.removeEventListener("vexa:terminal:focus-chat", focus);
  }, []);

  useEffect(() => { if (inputRef.current) resizeComposerTextarea(inputRef.current); }, [value]);
  useEffect(() => { attachmentsRef.current = attachments; }, [attachments]);
  useEffect(() => () => {
    for (const a of attachmentsRef.current) {
      if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
    }
  }, []);

  // Load history into an idle, empty session snapshot. Live turns stay in the per-session store so switching
  // sessions never redirects or clears an in-flight stream.
  useEffect(() => {
    const key = chatKey;
    const state = getChatState(key);
    if (state.loaded || state.loading || state.busy) return;
    updateChatState(key, (s) => ({ ...s, loading: true }));
    // The result is committed to the per-session `key`, so it is safe to apply even if this effect run
    // was cancelled (deps changed / StrictMode remount): a real session switch targets a different key,
    // and an identical-key remount wants exactly this data. Crucially `loading` is ALWAYS released here —
    // bailing on cancel previously left `loading: true` stuck, and the guard above then blocked every retry,
    // hanging the pane forever on "Loading conversation…".
    (async () => {
      try {
        const r = await fetch(`/api/sessions/${encodeURIComponent(session)}/history`);
        const data: { turns?: HistoryTurn[] } = await r.json();
        // THE PERSON'S WORDS ARE A FIELD, NOT SOMETHING TO RECOVER (F47). `user_text` is what they
        // typed, recorded by the worker beside the turn; `text` is the composed prompt the model
        // was given. When the field is there it is rendered as-is and the composed prompt is never
        // shown. `compactStoredUserText` runs only for records written before the field existed —
        // it strips the machinery off the front by sentinel, else by matching preamble wording,
        // which is the derivation that silently broke on 2026-09-02 and put the whole grounding
        // prompt in a grey user bubble.
        //
        // `raw` stays the stored prompt either way: the filters below test the SHAPE of what was
        // stored (an onboarding kickoff, a machinery-marked composed opening), and those marks live
        // in the prompt, not in the person's sentence.
        const compacted = (data.turns ?? []).map((t) =>
          t.role === "user"
            ? { ...t, text: historyUserText(t), raw: t.text }
            : { ...t, raw: t.text });
        const loaded: Turn[] = compacted
          // Drop a PURE onboarding kickoff (legacy: marker with no user reply). A grounding-wrapped reply
          // (marker + grounding + reply) is KEPT and compacted to just the reply by compactStoredUserText.
          .filter((t) => !(t.role === "user" && t.raw.includes(ONBOARDING_KICKOFF_MARK) && !t.raw.includes(ONBOARDING_REPLY_SEP)))
          // MACHINERY IS NEVER THE PERSON'S SPEECH. A turn the product composed (a `?ask=` preset
          // from an emailed link, a proposal chip's hidden kick) was hidden when it was sent and
          // must stay hidden when it is read back — the founder saw his own prepare kick returned
          // to him as a grey user bubble because nothing marked it. Unconditional, unlike the
          // onboarding filter above: a kick has no "and then the human replied" form.
          .filter((t) => !(t.role === "user" && t.raw.includes(MACHINERY_MARK)))
          // LEGACY, and dated: every composed opening sent BEFORE the mark existed (2026-09-02) is
          // already in a transcript and would keep surfacing. Those sessions cannot be rewritten —
          // they are the users' own records — so they are recognised by their shape instead, and
          // only in the one position a composed opening can occupy: the session's FIRST turn,
          // opening with a bracketed preset tag (`[prep] …`, `[minutes-review] …` — the same tag
          // the agent instructions key on), and long, because a preset body is an instruction block
          // and a person's first line is a sentence. Delete this clause once no live session
          // predates the mark.
          .filter((t, i, arr) => !(t.role === "user"
            && arr.findIndex((x) => x.role === "user") === i
            && t.text.length >= 200
            && /^\s*\[[a-z][a-z0-9_-]{0,63}\]\s/.test(t.text)
            // an onboarding reply also opens with a bracketed tag and is long — it is the HUMAN's
            // words wrapped in grounding, and the filter above already decided its fate.
            && !t.raw.includes(ONBOARDING_KICKOFF_MARK)))
          .map((t, i) =>
            t.role === "user"
              ? { id: `h-u-${i}`, role: "user", text: t.text }
              : { id: `h-a-${i}`, role: "agent", text: t.text, ops: (t.ops ?? []).map(historyOp), commit: t.commit });
        updateChatState(key, (s) => {
          if (s.loaded || s.busy || s.turns.length > 0) return { ...s, loading: false, loaded: true };
          return { ...s, turns: loaded, nextId: Math.max(s.nextId, loaded.length), loading: false, loaded: true };
        });
      } catch {
        // A failed fetch must always clear `loading` (never hang) and leave `loaded` false so it retries.
        updateChatState(key, (s) => (s.loaded ? s : { ...s, loading: false }));
      }
    })();
  }, [chatKey, session, subject]);

  const addFiles = (files: File[]) => {
    if (files.length === 0) return;
    setUploadError(null);
    setAttachments((current) => [
      ...current,
      ...files.map((file) => {
        const isImage = file.type.startsWith("image/");
        return {
          id: `att-${attachmentSeqRef.current++}`,
          file,
          isImage,
          previewUrl: isImage ? URL.createObjectURL(file) : undefined,
        };
      }),
    ]);
  };

  const removeAttachment = (id: string) => {
    setAttachments((current) => current.filter((a) => {
      if (a.id !== id) return true;
      if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
      return false;
    }));
  };

  const clearAttachments = () => {
    setAttachments((current) => {
      for (const a of current) {
        if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
      }
      return [];
    });
  };

  const uploadAttachments = async (): Promise<UploadedWorkspaceFile[]> => {
    const form = new FormData();
    for (const a of attachments) form.append("files", a.file, a.file.name || "upload");
    const r = await fetch("/api/workspace/upload", { method: "POST", body: form });
    if (!r.ok) {
      let detail = `Upload failed (${r.status})`;
      try {
        const data = await r.json() as { detail?: string };
        if (data.detail) detail = data.detail;
      } catch {
        // keep the status-derived message
      }
      throw new Error(detail);
    }
    const data = await r.json() as { files?: UploadedWorkspaceFile[] };
    return data.files ?? [];
  };

  const send = async (text: string, prompt = text, referenceSource = text, opts: { hidden?: boolean; ground?: boolean; scaffoldId?: string; intent?: ChatIntent } = {}) => {
    // hidden → no visible user bubble (system kickoffs); ground:false → don't append the active
    // meeting/file context (onboarding must not inherit whatever meeting happens to be focused).
    const { hidden = false, ground = true } = opts;
    const v = text.trim();
    // A HIDDEN turn is machinery, and it must be hidden in the RECORD, not only in this render.
    // Without the mark the prompt lands in the transcript as a plain `user` message and comes back
    // from `/api/sessions/<s>/history` on the next hydration as the person's own grey bubble.
    const rawBase = promptWithReferences(prompt, referenceSource.trim());
    const basePrompt = rawBase && hidden ? rawBase + MACHINERY_NOTE : rawBase;
    const key = chatKey;
    const sessionForSend = session;
    const state = getChatState(key);
    if (!v || !basePrompt || state.busy) return;
    const n = state.nextId;
    const agentId = `a-${n}`;
    const displayText = advertiseFocus ? appendReferenceToken(v, contextRef) : v.trim();
    const ctrl = new AbortController();
    const newTurns = hidden
      ? [{ id: agentId, role: "agent" as const, text: "", ops: [] }]
      : [{ id: `u-${n}`, role: "user" as const, text: displayText }, { id: agentId, role: "agent" as const, text: "", ops: [] }];
    updateChatState(key, (s) => ({
      ...s,
      turns: [...s.turns, ...newTurns],
      busy: true,
      loading: false,
      loaded: true,
      nextId: Math.max(s.nextId, n + 1),
      abort: ctrl,
    }));
    // Cold-start / mid-turn-drop robustness lives in streamChatTurn: a chat turn spawns a FRESH
    // per-dispatch worker (docker backend) that takes seconds to boot, and the turn is NEVER lost even
    // if the SSE closes early (durable, resumable output Stream). So instead of "No chat output arrived"
    // the instant a stream ends, it RESUMES from the last SSE cursor (Last-Event-ID) and keeps rendering.
    // A live STATUS LINE (turn.status, driven by onStatus below) keeps the pane VERBOSE about what's
    // happening — "Starting agent…", "Working · 12s", "Reconnecting…" — so a long think / tool run / a
    // broken SSE reads as alive, never a frozen blank. Real output (a delta / tool / terminal) clears it.
    // `since` is per-gap: cleared on output, re-stamped when the next quiet stretch begins, so the counter
    // measures the CURRENT wait (the useful "is it stuck?" signal), not total turn time.
    const setStatus = (phase: ChatPhase | null) =>
      patchAgentTurn(key, agentId, (t) => ({ ...t, status: phase ? { phase, since: t.status?.since ?? Date.now() } : null }));
    const p = ground ? promptWithActiveContext(basePrompt, contextRef, activeMeeting) : basePrompt;
    // The active center tab grounds the turn: a meeting passes {kind, platform, native_id, meeting_id} so
    // agent-api folds its live transcript into the prompt server-side; a file passes {kind, ref}.
    // P0 (cross-tenant leak fix): `meeting_id` is the meetings-domain ROW id (the mock's `id`) — the
    // transcript carrier keys on it, so grounding reads THIS row's transcript (`tc:meeting:{row_id}`),
    // never a DIFFERENT tenant's / an older row's under the shared native. `native_id` is display only.
    // The meeting's raw STATUS (+ title/when/workspace) rides along so agent-api branches the
    // grounding by lifecycle phase — prep (no transcript, steer preparation) / live (fold the live
    // stream) / post (fold the processed notes). A status-less payload keeps the legacy live path.
    const active = !ground || !contextRef
      ? undefined
      : contextRef.kind === "meeting"
        ? {
            kind: "meeting", native_id: contextRef.value, meeting_id: activeMeeting?.id,
            platform: meetingPlatformSlug(activeMeeting),
            status: activeMeeting?.live_status,
            title: activeMeeting ? meetingLabel(activeMeeting) : undefined,
            scheduled_at: activeMeeting?.scheduled_at,
            workspace_id: activeMeeting?.workspace_id,
          }
        : { kind: contextRef.kind, ref: contextRef.raw };
    // The CONTEXT BUNDLE (slice 1): tz + surface + focus + explicit include toggles. The focus
    // mirrors `active` for meeting/file (server prefers `context`); workspace/today are new
    // focus kinds the server folds itself. ground:false (onboarding) sends no bundle at all.
    const wireFocus: FocusPayload | null | undefined = !ground
      ? undefined
      : (active && (active.kind === "meeting" || active.kind === "file"))
        ? (active as FocusPayload)
        : bundleFocus;
    const context = !ground ? undefined : buildChatContext({
      activeList, activeTab, focus: wireFocus ?? null, includeSchedule,
    });
    // F40 — a tool call ENDS an assistant message, so the next interim text is a new paragraph.
    // The rule itself is `joinInterim` in chatStream.ts, where it is documented and tested; this is
    // only the flag it reads. See there for why the boundary is a tool call and not a delta count.
    let breakBeforeNextDelta = false;
    // THE HANDOVER (Vexa-ai/vexa#1584). A turn that spawns a background job is over the moment the
    // job starts, and the composer must be free THEN — not when this connection eventually closes,
    // two minutes later. From that point the flag belongs to whatever the person sends next, so
    // this send stops touching it: clearing `busy` in its own `finally` would clear a later turn's.
    let ownsBusy = true;
    const startedJobs = new Set<string>();
    try {
      const result = await streamChatTurn(
        // `scaffold_id` on the FIRST turn: dispatch reads the same record the panel rendered from.
        // `intent` (PRD decision 32) — an Extend/Create press is an ACT on a named file, not a
        // sentence; it travels typed so the server can turn it into a preset without parsing prose.
        { prompt: p, session: sessionForSend, active, context, scaffold_id: opts.scaffoldId, intent: opts.intent },
        {
          onStarting: () => {},  // visual is driven by onStatus (below); the stream still signals cold-start here
          onStatus: (phase) => setStatus(phase),
          onDelta: (text) => patchAgentTurn(key, agentId, (t) => {
            const joined = joinInterim(t.text ?? "", text, breakBeforeNextDelta);
            breakBeforeNextDelta = false;
            return { ...t, status: null, text: joined };
          }),
          // A FILE THIS TURN WROTE. Re-emitted for the shell, which owns the chat record's tabs —
          // this surface never opens a document itself (F41).
          onArtifact: (a) => window.dispatchEvent(new CustomEvent(ARTIFACT_EVENT, { detail: a })),
          // TERMS THIS TURN PUBLISHED for a meeting's transcript (PRD decision 35). Same seam and
          // same reason as the artifact above: the chips are part of the chat's record and the
          // transcript renders that record — this surface forwards and stores nothing.
          onTerms: (t) => window.dispatchEvent(new CustomEvent(TERMS_EVENT, { detail: t })),
          onTool: (tool, args) => {
            breakBeforeNextDelta = true;      // F40 — the assistant message ended here
            const op = toolOp(tool, args);
            // The workspace tree JUST changed. Drop the doc-link caches (60s TTL) or every entity
            // chip in the reply that names this new file resolves to "not found" — which is the
            // whole reason a turn's own chips were dead on arrival.
            if (op.wrote) invalidateDocLinkCaches();
            // the step that was running has finished — the arrival of the next one IS its completion
            patchAgentTurn(key, agentId, (t) => ({
              ...t, status: null,
              ops: [...t.ops.map((o) => (o.status === "running" ? { ...o, status: "done" as const } : o)), op],
            }));
          },
          onCommit: (sha) => {
            invalidateDocLinkCaches();
            // The panel may be showing a document this turn just CREATED. A chat declares its
            // tabs up front (PRD decision 18), so the setup conversation opens five pages
            // before four of them exist — and without this they stay "no page here yet" until
            // something else happens to remount them. A commit is the durable moment the files
            // became real, so it is the one that tells the panel to look again.
            window.dispatchEvent(new CustomEvent(WORKSPACE_COMMIT_EVENT));
            patchAgentTurn(key, agentId, (t) => ({ ...t, commit: sha }));
          },
          // THE TURN HANDED ITS WORK OFF AND IS DONE. Settle its steps, drop the live indicator and
          // give the composer back — the acknowledgement line has already arrived as a delta, and
          // everything from here belongs to the job chip below.
          onJobStarted: (j) => {
            ownsBusy = false;
            startedJobs.add(j.jobId);
            patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, ops: settleOps(t.ops) }));
            updateChatState(key, (s) => ({ ...s, busy: false, jobs: startJob(s.jobs, { id: j.jobId, kind: j.kind, target: j.target }) }));
          },
          onJobStep: (jobId, tool) => updateChatState(key, (s) => ({
            ...s, jobs: stepJob(s.jobs, jobId, toolOp(tool).label.split(" · ").pop() ?? ""),
          })),
          // ONE LINE, ALWAYS — landed or died. A job that finishes in silence is indistinguishable
          // from one that is still running, and the chip has just gone.
          onJobEnd: ({ jobId, line }) => {
            startedJobs.delete(jobId);
            updateChatState(key, (s) => ({
              ...s,
              jobs: endJob(s.jobs, jobId),
              turns: line ? [...s.turns, { id: `j-${jobId}`, role: "agent" as const, text: line, ops: [] }] : s.turns,
            }));
          },
          onRejected: () => patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, rejected: "workspace.v1 violation — reverted" })),
          onModelFailure: (reply) => patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, text: (t.text ?? "") + (t.text ? "\n\n" : "") + `Model inference failed${reply ? `: ${reply}` : "."}` })),
          // THE TURN STOPPED EARLY (F89) — not the same thing as the model failing. Keep whatever
          // the turn did produce and say plainly that it is partial, so the person knows to ask
          // again rather than reading half an answer as the whole one.
          onTruncated: (reason, partial) => patchAgentTurn(key, agentId, (t) => {
            const body = t.text ?? partial ?? "";
            return { ...t, status: null, text: body + (body ? "\n\n" : "") + `_${reason}_` };
          }),
          onError: (msg) => patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, text: (t.text ?? "") + (t.text ? "\n\n" : "") + presentError(new Error(msg)).headline })),
          onProgress: () => { if (stickToBottomRef.current) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); },
        },
        { signal: ctrl.signal },
      );
      if (!result.aborted && !result.terminal && startedJobs.size === 0) {
        // The turn never reached a clean end even after resuming past the hard cap — the connection is
        // genuinely lost. Say so (fail-loud, P18): append a note if there was partial output, else the
        // timeout copy. The worker may still finish server-side, so point the user at a reopen.
        patchAgentTurn(key, agentId, (t) => {
          const base = t.text ?? "";
          return { ...t, status: null, text: base
            ? base + "\n\n_Connection lost before the reply finished — reopen the chat to see the rest if it lands._"
            : "The agent didn't respond before timing out. Reopen the chat to see the reply if it lands." };
        });
      } else {
        // THE TICK ONLY AT THE END (F66): the last step settles when the turn does, never before.
        patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, ops: settleOps(t.ops) }));
      }
    } catch (e) {
      if ((e as Error)?.name === "AbortError") patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, text: (t.text ?? "") + (t.text ? "\n\n" : "") + "_stopped_" }));
      else patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, text: (t.text ?? "") + (t.text ? "\n\n" : "") + presentError(e).headline }));
    } finally {
      // whatever happened — abort, error, timeout — no step is left spinning. A spinner that
      // outlives its turn is the same lie as a tick that precedes it.
      patchAgentTurn(key, agentId, (t) => ({ ...t, ops: settleOps(t.ops) }));
      if (ownsBusy) updateChatState(key, (s) => ({ ...s, busy: false, abort: null }));
      // A JOB CHIP MUST NOT OUTLIVE ITS CONNECTION, for the same reason a spinner must not outlive
      // its turn. `onJobEnd` removes each one as it lands, so anything still in this set means the
      // stream died with that job unaccounted for — say so, and stop spinning.
      for (const lost of startedJobs) {
        updateChatState(key, (s) => ({
          ...s, jobs: endJob(s.jobs, lost),
          turns: [...s.turns, { id: `j-${lost}`, role: "agent" as const, text: "_Lost the connection to that background job — it may still have finished; reopen the page to see._", ops: [] }],
        }));
      }
    }
  };

  const stop = () => {
    getChatState(chatKey).abort?.abort();
    updateChatState(chatKey, (s) => ({ ...s, busy: false, abort: null }));
  };

  // A canvas keyword chip (or any harness `actions.ask`) asks the visible chat a question: reveal the rail
  // and stream the answer here. sendRef keeps the latest `send` closure so the listener stays stable.
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    const onAsk = (e: Event) => {
      const detail = (e as CustomEvent<{ prompt?: string; display?: string; hidden?: boolean; ground?: boolean; session?: string; scaffoldId?: string; intent?: ChatIntent }>).detail;
      const prompt = detail?.prompt;
      if (!prompt) return;
      // A SESSION-TARGETED ask must never land in whichever chat happens to be visible (the
      // workspace-scaffold kickoff once fired into the org-setup thread mid-switch). Not ours →
      // stash it; the target session's Chat consumes it the moment it mounts.
      if (detail?.session && detail.session !== session) {
        try { localStorage.setItem(`vexa.pendingAsk.${detail.session}`, JSON.stringify({ prompt, display: detail.display, hidden: detail.hidden, ground: detail.ground, scaffoldId: detail.scaffoldId })); } catch { /* ignore */ }
        return;
      }
      if (layout.store.getState().rightCollapsed) layout.toggleRight();
      // `display` — what the READER sees when it is not what the agent gets: a chip whose label is
      // the user's own sentence renders as their message, and the grounding it carries does not.
      void sendRef.current(detail?.display || prompt, prompt, prompt, { hidden: detail?.hidden, ground: detail?.ground, scaffoldId: detail?.scaffoldId, intent: detail?.intent });
    };
    window.addEventListener(ASK_CHAT_EVENT, onAsk);
    return () => window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  }, [layout, session]);

  // Consume a stashed session-targeted ask once THIS chat is the target and idle.
  useEffect(() => {
    const key = `vexa.pendingAsk.${session}`;
    let raw: string | null = null;
    try { raw = localStorage.getItem(key); } catch { /* ignore */ }
    if (!raw) return;
    const t = setTimeout(() => {
      try { localStorage.removeItem(key); } catch { /* ignore */ }
      try {
        const d = JSON.parse(raw as string) as { prompt: string; display?: string; hidden?: boolean; ground?: boolean; scaffoldId?: string };
        if (d.prompt) void sendRef.current(d.display || d.prompt, d.prompt, d.prompt, { hidden: d.hidden, ground: d.ground, scaffoldId: d.scaffoldId });
      } catch { /* ignore */ }
    }, 600);
    return () => clearTimeout(t);
  }, [session]);

  // ── DELETED 2026-09-02 (F36): the cached onboarding greeting ────────────────────────────────
  //
  //  This listener wrote a canned agent turn into an empty chat the moment OnboardingGate fired its
  //  seed — instantly, with no model round-trip — and armed the chat so the person's first reply
  //  carried the discovery-loop grounding. It is what put "I'm your agent here … paste a meeting
  //  link" in front of the founder in a chat he had never made.
  //
  //  A first turn nobody typed is machinery speaking as the product, and the founder's ruling is
  //  that a new chat says nothing. The grounding it used to arm still reaches the agent — the setup
  //  proposal chip carries it in its own kick (minutes/proposals.ts), which is a chip the person
  //  actually pressed rather than a greeting they were handed.

  const focusInput = () => window.setTimeout(() => inputRef.current?.focus(), 0);
  const selectSession = (id: string) => { layout.setActiveSession(id); focusInput(); };
  const newChat = () => selectSession(`chat-${Date.now().toString(36)}`);

  // Messages typed while the agent is mid-turn QUEUE instead of silently dropping: the bubble
  // appears immediately (dimmed, "queued"), and fires as its own turn the moment the current one
  // ends. A queued bubble that never sends (navigation away) is dropped with its ref — it was
  // never in the session.
  const queuedRef = useRef<{ id: string; display: string; prompt: string }[]>([]);
  useEffect(() => {
    if (busy) return;
    const next = queuedRef.current.shift();
    if (!next) return;
    updateChatState(chatKey, (s) => ({ ...s, turns: s.turns.filter((t) => t.id !== next.id) }));
    void send(next.display, next.prompt, "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);

  const onSubmit = async () => {
    const v = value.trim();
    const hasAttachments = attachments.length > 0;
    if ((!v && !hasAttachments) || uploading) return;
    // the user WROTE here — the minutes rail keeps a `touched` flag per chat and this is its
    // only writer. Fired before the busy/queue branches, because queueing is still authorship.
    //
    // The TEXT rides along (F38). The rail names a chat from its first human turn, and this is the
    // only place in the client that knows a turn is a human's: an agent turn never reaches here,
    // and a composed opening arrives through the ask-chat path, not through the composer. So the
    // name is taken from a message the person typed, by construction rather than by a check.
    window.dispatchEvent(new CustomEvent(CHAT_TOUCHED_EVENT, { detail: { session, text: v } }));
    if (busy) {
      if (!v || hasAttachments) return;   // queue plain text only; attachments wait for idle
      const qid = `q-${Date.now().toString(36)}`;
      queuedRef.current.push({ id: qid, display: v, prompt: isRoutineCommand(v) ? routineCreationPrompt(v) : v });
      updateChatState(chatKey, (s) => ({ ...s, turns: [...s.turns, { id: qid, role: "user", text: v }] }));
      setValue("");
      return;
    }
    stickToBottomRef.current = true;  // sending re-attaches follow-the-stream
    window.setTimeout(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }), 0);
    if (!hasAttachments && isRoutineCommand(v)) { void send(v, routineCreationPrompt(v)); setValue(""); return; }
    if (!hasAttachments && v.startsWith("/")) { const sk = commands.querySkills(v)[0]; if (sk) { void commands.execute(sk.id, v); setValue(""); return; } }
    let prompt = isRoutineCommand(v) ? routineCreationPrompt(v) : v;
    let displayText = v;
    let referenceSource = v;
    if (hasAttachments) {
      setUploading(true);
      setUploadError(null);
      let uploaded: UploadedWorkspaceFile[];
      try {
        uploaded = await uploadAttachments();
      } catch (e) {
        setUploadError((e as Error)?.message || "Upload failed");
        setUploading(false);
        return;
      }
      setUploading(false);
      prompt = attachmentPrompt(prompt, uploaded);
      referenceSource = [v, uploaded.map((f) => `@file:${f.path}`).join("\n")].filter(Boolean).join("\n");
      displayText = displayText || `Attached files: ${uploaded.map((f) => f.name).join(", ")}`;
      clearAttachments();
    }
    // ── DELETED 2026-09-02 (F36/F37): the two grounding arms ────────────────────────────────
    //
    //  The first attached the discovery-loop grounding to whatever reply followed the cached
    //  greeting; the second attached the org-setup flow grounding to the first turn of an
    //  `org-setup` session. Both are gone with the paths that armed them: the greeting is deleted,
    //  and the `org-setup` session id was minted by the rail's seeding and by nothing else, so the
    //  branch is now unreachable — which, per the founder's stale-code ruling, means it is deleted
    //  rather than left as a trap for the next person who wonders why it never fires. The admin
    //  conversation is a SCAFFOLD (`kind: "admin-setup"`), opened and grounded from its record.
    void send(displayText, prompt, referenceSource);
    setValue("");
  };

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData.items)
      .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
      .map((item) => item.getAsFile())
      .filter((file): file is File => !!file);
    if (files.length === 0) return;
    e.preventDefault();
    addFiles(files);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    addFiles(Array.from(e.dataTransfer.files));
  };

  const slash = value.startsWith("/");
  const skills = slash ? commands.querySkills(value) : [];

  // F66 · THE COMPOSER SAYS WHAT IS HAPPENING. The step line lives up in the transcript, which
  // scrolls away on a long turn — so the state also sits beside the stop button, where the reader's
  // hand already is: "working · 18 steps · entity_upsert". Cleared the moment the turn completes.
  const liveOps = busy ? (turns[turns.length - 1] as AgentTurn | undefined)?.ops ?? [] : [];
  const liveStep = liveOps.length ? liveOps[liveOps.length - 1] : null;
  const liveLabel = liveStep ? (liveStep.label.split(" · ").pop() ?? liveStep.label) : "";
  const turnState = busy
    ? ["working", liveOps.length ? `${liveOps.length} step${liveOps.length === 1 ? "" : "s"}` : "", liveLabel]
        .filter(Boolean).join(" · ")
    : "";
  // …AND WHATEVER IS RUNNING IN THE BACKGROUND (Vexa-ai/vexa#1584). A job is not `busy` — the
  // composer is free while it runs, which is the whole point — so it needs its own half of this
  // line, or a person who pressed Extend and carried on typing has no way to tell it is still
  // happening. Rendered in the same place and the same shape as the turn's own state; the job's
  // TARGET is what tells two of them apart.
  const liveState = [turnState, jobLine(chatState.jobs)].filter(Boolean).join("   ");

  const composer = (
    <>
      {slash && skills.length > 0 && (
        <div style={{ border: "1px solid var(--line2)", borderRadius: 11, background: "var(--panel)", overflow: "hidden" }}>
          {skills.map((c) => <div key={c.id} onMouseDown={() => setValue(c.skill! + " ")} style={{ display: "flex", gap: 10, padding: "9px 12px", cursor: "pointer", fontSize: 13 }}><code style={{ fontFamily: "var(--mono)", color: "var(--accent)", minWidth: 88 }}>{c.skill}</code><span style={{ color: "var(--t3)", fontSize: 12 }}>{c.title}</span></div>)}
        </div>
      )}
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDrop}
        style={{ border: "1px solid var(--line2)", borderRadius: 12, background: "var(--panel)", padding: "9px 12px", display: "flex", flexDirection: "column", gap: 7 }}
      >
        {((advertiseFocus && contextRef) || (!minutesOnly() && (ambientEligible || includeSchedule === true)) || (bundleFocus && (bundleFocus.kind === "workspace" || bundleFocus.kind === "today"))) && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, flexWrap: "wrap" }}>
            {/* ambient schedule chip — the context bundle's always-visible half: on = the agent
                sees today's schedule; × turns it off; ghost chip re-adds. HIDDEN in minutes mode
                for now (founder 2026-08-22) — the context still flows, the chip just doesn't. */}
            {minutesOnly() ? null : ambientOn ? (
              <span title="The agent sees your schedule (today, upcoming, live) on this surface"
                style={{ flex: "none", display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, color: "var(--t2)", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 999, padding: "2px 4px 2px 9px" }}>
                <Icon name="cal" size={10} /> Schedule · today
                <button aria-label="Remove schedule context" title="Remove schedule context for this session" onClick={() => setAmbient(false)}
                  style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", padding: 2 }}><Icon name="x" size={10} /></button>
              </span>
            ) : ambientEligible ? (
              <button onClick={() => setAmbient(null)} title="Include your schedule in the agent's context"
                style={{ flex: "none", display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--t3)", background: "transparent", border: "1px dashed var(--line2)", borderRadius: 999, padding: "2px 9px", cursor: "pointer" }}>
                + schedule
              </button>
            ) : null}
            {/* B4 carve: the meeting focus is ONE chip — `Preparing · Title ×` — never a mono
                uppercase label plus a second raw-id Focus chip for the same meeting. */}
            {advertiseFocus && contextRef && contextRef.kind === "meeting" && activeMeeting ? (() => {
              const mode = MODE_CHIP[meetingPhase(activeMeeting)];
              return (
                <span title={`This chat is grounded in the meeting's ${mode.label.toLowerCase()} state`}
                  style={{ flex: "none", display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11.5,
                    fontWeight: 600, color: mode.color, background: mode.bg, borderRadius: 999,
                    padding: "2px 5px 2px 10px", maxWidth: 260, minWidth: 0 }}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>
                    {mode.label} · {meetingLabel(activeMeeting)}
                  </span>
                  <button aria-label="Clear focus" title="Clear focus" onClick={() => setFocusCleared(true)}
                    style={{ background: "none", border: "none", color: mode.color, opacity: 0.7, cursor: "pointer", display: "flex", padding: 2, flex: "none" }}><Icon name="x" size={10} /></button>
                </span>
              );
            })() : advertiseFocus && contextRef ? (
              <>
                <span style={{ color: "var(--t3)", fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em", flex: "none" }}>Focus</span>
                <ReferenceChip refToken={contextRef} />
                <button aria-label="Clear focus" title="Clear focus" onClick={() => setFocusCleared(true)} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", padding: 0, marginLeft: 2, flex: "none" }}><Icon name="x" size={12} /></button>
              </>
            ) : null}
            {(!advertiseFocus || !contextRef) && bundleFocus && (bundleFocus.kind === "workspace" || bundleFocus.kind === "today") && (
              <>
                <span style={{ color: "var(--t3)", fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em", flex: "none" }}>Focus</span>
                <span style={{ flex: "none", display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, color: bundleFocus.kind === "workspace" ? "var(--blue)" : "var(--t2)", background: bundleFocus.kind === "workspace" ? "var(--bluebg)" : "var(--panel2)", border: "1px solid var(--line)", borderRadius: 6, padding: "1px 7px" }}>
                  <Icon name={bundleFocus.kind === "workspace" ? "panel" : "cal"} size={10} />
                  {bundleFocus.kind === "workspace" ? `Workspace · ${bundleFocus.slug}` : "Today"}
                </span>
                <button aria-label="Clear focus" title="Clear focus" onClick={() => setFocusCleared(true)} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", padding: 0, marginLeft: 2, flex: "none" }}><Icon name="x" size={12} /></button>
              </>
            )}
          </div>
        )}
        <ComposerReferences text={value} />
        <AttachmentChips attachments={attachments} onRemove={removeAttachment} />
        {uploadError && <div style={{ color: "var(--danger)", fontSize: 12, lineHeight: 1.35 }}>{uploadError}</div>}
        {micError && <div style={{ color: "var(--danger)", fontSize: 12, lineHeight: 1.35 }}>{micError}</div>}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ATTACHMENT_ACCEPT}
          onChange={(e) => { addFiles(Array.from(e.target.files ?? [])); e.currentTarget.value = ""; }}
          style={{ display: "none" }}
        />
        <div style={{ display: "flex", alignItems: "flex-end", gap: 9 }}>
          <span style={{ fontFamily: "var(--mono)", color: "var(--t3)", fontSize: 13, height: 30, display: "flex", alignItems: "center", flex: "none" }}>/</span>
          <textarea
            ref={inputRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onInput={(e) => resizeComposerTextarea(e.currentTarget)}
            onPaste={onPaste}
            onKeyDown={(e) => {
              if (e.key !== "Enter" || e.shiftKey) return;
              e.preventDefault();
              void onSubmit();
            }}
            placeholder={contextRef?.kind === "meeting" && activeMeeting
              ? MODE_PLACEHOLDER[meetingPhase(activeMeeting)]
              : "Type / for skills, or ask the agent…"}
            disabled={uploading}
            rows={1}
            style={{ flex: 1, background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 14, lineHeight: "20px", minWidth: 0, minHeight: 28, maxHeight: MAX_TEXTAREA_HEIGHT, resize: "none", overflowY: "hidden", padding: "4px 0", margin: 0, fontFamily: "inherit" }}
          />
          <button type="button" aria-label="Attach files" title="Attach files" disabled={busy || uploading} onClick={() => fileInputRef.current?.click()}
            style={{ background: "transparent", color: "var(--t3)", border: "1px solid var(--line2)", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: busy || uploading ? "default" : "pointer", flex: "none", opacity: busy || uploading ? 0.6 : 1 }}>
            <Icon name="paperclip" size={15} />
          </button>
          <button type="button"
            aria-label={mic === "rec" ? "Stop recording" : "Dictate"}
            title={mic === "rec" ? "Stop recording (transcribes into the composer)" : mic === "stt" ? "Transcribing…" : "Dictate"}
            disabled={uploading || mic === "stt"}
            onClick={() => void toggleMic()}
            style={{
              background: mic === "rec" ? "var(--accentbg)" : "transparent",
              color: mic === "rec" ? "var(--accent)" : "var(--t3)",
              border: `1px solid ${mic === "rec" ? "var(--accent)" : "var(--line2)"}`,
              width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center",
              cursor: uploading || mic === "stt" ? "default" : "pointer", flex: "none", opacity: mic === "stt" ? 0.6 : 1,
            }}>
            {mic === "stt"
              ? <span className="vx-op-spin" style={{ width: 12, height: 12, border: "2px solid var(--line2)", borderTopColor: "var(--t2)", borderRadius: "50%", display: "block" }} />
              : <Icon name="mic" size={15} />}
          </button>
          {/* The live line sits OUTSIDE the busy branch it used to live in: a background job runs
              with the composer free, so the one place that says what the agent is doing has to be
              able to say it beside a Send button as well as beside a Stop one. */}
          {liveState && (
            <span data-live-state title={liveState}
              style={{ flex: "0 1 auto", minWidth: 0, marginRight: 8, fontFamily: "var(--mono)", fontSize: 11,
                color: "var(--t3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {liveState}
            </span>
          )}
          {busy
            ? <button aria-label="Stop" title="Stop" onClick={stop} style={{ background: "var(--panel2)", color: "var(--t1)", border: "1px solid var(--line2)", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", flex: "none" }}><span style={{ width: 10, height: 10, background: "var(--t1)", borderRadius: 2, display: "block" }} /></button>
            : <button aria-label="Send" disabled={uploading} onClick={() => void onSubmit()} style={{ background: "var(--accent)", color: "var(--on-accent)", border: "none", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: uploading ? "default" : "pointer", flex: "none", opacity: uploading ? 0.7 : 1 }}><Icon name="send" size={16} /></button>}
        </div>
      </div>
    </>
  );

  return (
    <AgentWindow top={<ChatHeader subject={subject} session={session} onSelectSession={selectSession} onNewChat={newChat} onClose={() => layout.toggleRight()} />} scrollRef={scrollRef} composer={composer}>
      {/* THE EMPTY STATE. The centered "What organisation are you? / Just the name is enough — I'll
          research the rest…" card that used to sit here is DELETED (F37): it was the pre-scaffold
          admin onboarding, reachable only from an `org-setup` session the rail's own seeding
          planted, and it promised the reader a research step that does not exist. The founder met
          it in a chat he never made — *"I explain this as stale code."*
          What is left renders the meeting greeting when there IS a meeting, and otherwise renders
          nothing but whatever the host put in `emptyExtra`. */}
      <ChatConversation turns={turns} busy={busy || loading} surface={surfaceOf(session, activeTab)} empty={
        <div style={{ color: minutesOnly() ? "var(--t2)" : "var(--t3)", fontSize: 13, textAlign: minutesOnly() ? "left" : "center", lineHeight: 1.6, maxWidth: 560, margin: minutesOnly() ? "26px auto 0" : "40px 0 0", padding: minutesOnly() ? "0 22px" : 0 }}>
            {loading ? "Loading conversation…" : (minutesOnly()
              ? minutesEmptyGreeting(session)
              : "Ask the agent to record, research, or restructure knowledge — it writes to your git workspace and commits.")}
            {/* NOT while the history is still loading: a chip that appears and then vanishes under
                an arriving conversation is worse than one that arrives a beat late. */}
            {!loading && emptyExtra}
          </div>} />
    </AgentWindow>
  );
}

// Agent /-skills — absent in meetings-only mode (NEXT_PUBLIC_TERMINAL_MODE=meetings), where the chat
// rail itself doesn't render (Workbench) and the proxy refuses agent paths.
if (!meetingsOnly()) {
  registerCommand({ id: "skill.research", title: "Research and file to the workspace", skill: "/research", run: () => {} });
  registerCommand({ id: "skill.draft", title: "Draft an email or doc", skill: "/draft", run: () => {} });
  registerCommand({ id: "skill.routine", title: "Create a scheduled routine", skill: ROUTINE_COMMAND, run: () => {} });
}

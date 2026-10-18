"use client";
import { privateApproval, type PrivateApproval } from "./approvalCard";
import { Identity } from "../ui-kit/Identity";
import { useRoom } from "./roomState";
import { ComposerSlotProvider } from "./composerSlot";
import { setChatActivity } from "./chatActivity";
/** Chat — the persistent right-rail agent window. Streams a real agent turn over /api/chat (SSE) into the
 *  turn timeline, surfacing each tool-call as a visible operation (read/search/edit/git/web) with status,
 *  then the message + commit / rejection badge. The composer carries the active center-tab reference. */
import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ClipboardEvent, type ReactNode } from "react";
import { minutesOnly } from "../platform/mode";
import { useFeature } from "../platform/features";
import { liveMeetingsNow } from "./liveMeetings";
import { decideProposal, type ProposalChoice } from "./targetProposal";
import { useService, useStore, CommandServiceId, ARTIFACT_EVENT, CHOOSE_TARGET_EVENT, CHAT_ARTIFACTS_EVENT, ASK_CHAT_EVENT, CALL_PLACED_EVENT, CALL_START_EVENT, OPEN_SESSION_EVENT, CHAT_TOUCHED_EVENT, FOCUS_WORKSPACE_EVENT, MACHINERY_MARK, OPEN_PAGE_EVENT, WORKSPACE_COMMIT_EVENT, MACHINERY_NOTE, ONBOARDING_KICKOFF_MARK, ONBOARDING_REPLY_SEP, ROOM_SHOWING_EVENT } from "../platform";
import { LayoutServiceId, type ActiveTab } from "../platform/layout";
import { registerCommand, type TabProps } from "../contributions";
import { meetingsOnly } from "../platform/mode";
import { AgentLine, AgentWindow, Conversation, OPEN_CHAT_EVENT, opIcon, type FromAgent, type Turn, type Op } from "./agent-window";
import { Chip, ChipRow, Fold, Icon, IconButton, Menu, mergeSources, readSources, type MenuItem } from "../ui-kit";
import { Crosshair, Ellipsis, Mic, Paperclip, Plus, Radio, Send, Square } from "lucide-react";
import { BundleImportCards, useBundleImports } from "./ChatBundleImport";
import { invalidateDocLinkCaches } from "../ui-kit/docLinks";
import { micErrorMessage, startStreamingDictation, type StreamingDictation } from "../ui-kit/micDictation";
import { createComposerDictation, type ComposerDictation } from "../ui-kit/composerDictation";
import { toolResultUiEvent, toolUiEvent } from "./toolUiEvents";
import "./dictation.css";
import { sessionTitle, type SessionSummary } from "./sessions";
import { faultTone } from "./faults";
import { knownAsMentionable, listKnownPeople, listMentionable, listSessions, sessionRoomLines, type MentionablePerson } from "./sessionsApi";
import { activeMention, callInviteDraft, insertMention, insertReference, mentionsInText, pickerRows, type Mention, type PickerRow } from "./mentions";
import { checkRoomTags, checkShare, shareWith, type ShareCheck } from "./shareFlow";
import type { TagAnswer } from "./roomPeopleApi";
import { TagCards } from "./TagCard";
import { MentionChips, MentionChoice, MentionPicker, ShareConfirm } from "./MentionUI";
import { callFromThisView, joinInterim, streamChatTurn, type ChatPhase, type HeldWrite } from "./chatStream";
import { readFault } from "./faults";
import { createStickToBottom, type StickToBottom } from "./stickToBottom";
import { buildChatContext, focusTarget, readIncludeSchedule, scheduleEligible, writeIncludeSchedule, type FocusPayload } from "./chatContext";
import { useLiveMeetings } from "./liveMeetings";
import { meetingPhase, type MeetingMock, type MeetingPhase } from "./meetingModel";
import { presentError } from "../data/apiClient";
import { ModelPicker } from "./ModelPicker";
import { promptCarriesActiveContext } from "./surfaceSync";
import { isPageIntent, type ChatIntent } from "./chatIntent";
import { foldSignalWakes, wakeLabel } from "./signalWake";
import { actEnded, actNoted, actQueued, actSending, actSettled, actStarted, actStepped } from "./actState";
import { actTarget, endJob, isJobIntent, jobLine, jobTarget, noteJob, promoteJob, queueJob, startJob, stepJob, type JobRec } from "./jobs";
// THE CHAT'S INBOX (Vexa-ai/vexa#1610) — everything submitted is on the SERVER at once, and the
// queued rows are read back from it rather than remembered here. See `surfaces/inbox.ts`.
import { blockSubmission, canSendNow, withdrawQueued, claimInboxRow, fetchPending, flushOutbox, newSubmissionId, readOutbox, reconcileInbox, sendNow, stopTurn, streamIdAfter, submitToInbox, SubmitRefused, turnsToWatch, attachPoint } from "./inbox";
import { MINUTES_ONBOARDING_GREETING, MINUTES_PREP_GREETING } from "../platform/turnMarks";
import { TERMS_EVENT } from "../platform/events";
import { FolderImportedCards, useFolderImportedCards } from "./FolderImport";
import { requestFolderImport } from "./folderDrop";
import { PasteChip, UserMessage, isLongPaste, withPastes } from "./pastedText";
import { RefChip } from "../ui-kit/RefChip";
import { AskSessionProvider } from "../ui-kit/InlineCard";
import { Button as KitButton } from "../ui-kit/primitives/Button";
import { ModelChip } from "./usage/ModelChip";
import { NEW_CHAT_AT_LEVEL_EVENT } from "../platform/events";
import { splitRefs } from "../ui-kit/universalRefs";
import { longPressProps, meetingLinkIn, pasteToChip, raiseMessageActions, referenceFromPaste, sendVexa } from "./reference";
/** The conversation a message line belongs to, for its reference (`message:<session>/<seq>`). */
const MessageScope = createContext<string | null>(null);
import { ATTACHMENT_ACCEPT, AttachmentTray, FileDropZone, ImagePreview, MessageAttachments, attachmentSrc, attachmentsMark, originalName, pastedFiles, refusalFor, uploadAttachment, type ComposerAttachment, type MessageAttachment, type UploadedWorkspaceFile } from "./attachments";

/** classify a tool name into one of the op icons so the operation line reads at a glance */
export function toolOp(tool: string, args?: Record<string, unknown>): Op {
  // `mcp__vexa__entity_upsert` → `entity_upsert`. The namespace is ours, not the reader's, and the
  // founder's own words for what he was watching were "entity_upsert".
  tool = tool.replace(/^mcp__[^_]+(?:_[^_]+)*?__/, "");
  const t = tool.toLowerCase();
  // The harness's Skill tool loads instructions; the step is named by the skill, not "Skill".
  if (t === "skill") {
    const skill = typeof args?.skill === "string" && args.skill ? (args.skill as string) : undefined;
    return { icon: opIcon.tool, label: skill ? `Loading skill · ${skill}` : "Loading skill", status: "running" };
  }
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
  // A PAGE IS NAMED BY ITS TITLE, NEVER ITS FILE (founder 2026-10-16: "ait-lisbon-next-meetup-nov-2026.md")
  const base = file ? file.split("/").filter(Boolean).pop() : undefined;
  const stem = base ? base.replace(/\.(md|markdown|mdx)$/i, "") : undefined;
  const name = stem ? (/^readme$/i.test(stem) ? "README" : (stem.replace(/[-_]+/g, " ").trim().replace(/^./, (c) => c.toUpperCase()))) : undefined;
  // RUNNING, not done (F66). Every op was appended with `status: "done"`, so the step line rendered
  // a green TICK from the first tool call — an 18-step turn showed a finished-looking line that
  // never moved. Founder: *"i know it's working now, but it just stays like it's stale."* The op
  // that just started IS the one in progress; `onTool` marks the previous one done as it arrives,
  // and the turn's end marks the last one.
  // A HARNESS TOOL IS NEVER NAMED (founder 2026-10-16: "Searching · Glob"): Glob, Grep, Bash, Read…
  // are the agent's plumbing; the step says what it is doing in human words, or just the verb.
  const harness = /^[A-Z][A-Za-z]*$/.test(tool);
  if (harness && !name) {
    const words = /glob|grep|search|find|^ls$/.test(t) ? "Looking through your files"
      : /web|fetch/.test(t) ? "Reading the web"
      : /bash|exec|run|task|todo/.test(t) ? "Working"
      : verb === tool ? "Working" : verb;
    return { icon, label: words, status: "running", file, wrote };
  }
  return { icon, label: name ? `${verb} · ${name}` : (verb === tool ? tool : `${verb} · ${tool}`), status: "running", file, wrote };
}

/** Close any step still marked running — the turn is over, so nothing in it is in progress. Errors
 *  keep their own status: a step that FAILED did not succeed because the turn ended. */
function settleOps(ops: Op[]): Op[] {
  return ops.some((o) => o.status === "running")
    ? ops.map((o) => (o.status === "running" ? { ...o, status: "done" as const } : o))
    : ops;
}

/** The status a finished call leaves its step in, when the call did not simply work. */
export function settledStatus(outcome: string | undefined, ok: boolean): Op["status"] | null {
  if (outcome === "held") return "held";
  if (outcome === "refused" || outcome === "failed" || !ok) return "error";
  return null;
}

/** Mark the step a result belongs to — by its call id, else the newest step of that tool still
 *  unsettled — with how the call ended. */
export function markOp(ops: Op[], callId: string | undefined, tool: string, status: Op["status"]): Op[] {
  let at = callId ? ops.findIndex((o) => o.callId === callId) : -1;
  if (at < 0) {
    const label = toolOp(tool).label;
    for (let i = ops.length - 1; i >= 0; i--) {
      if ((ops[i].status === "running" || ops[i].status === "done") && ops[i].label.split(" · ")[0] === label.split(" · ")[0]) { at = i; break; }
    }
  }
  if (at < 0) return ops;
  return ops.map((o, i) => (i === at ? { ...o, status } : o));
}

/** The steps that wrote a file the commit kept back (`paths` are relative to the workspace). */
export function markWithheld(ops: Op[], paths: string[]): Op[] {
  const held = (file: string) => paths.some((p) => p && (file === p || file.endsWith(`/${p}`)));
  const hit = ops.some((o) => o.wrote && o.file && held(o.file));
  return ops.map((o) => (o.wrote && o.file && (hit ? held(o.file) : true) && o.status !== "error" ? { ...o, status: "held" as const } : o));
}

/** The server's `held` fields as the approval card's shape. */
export function heldApproval(h: HeldWrite): PrivateApproval | null {
  if (!h || typeof h.code !== "string" || !h.code) return null;
  return { code: h.code, workspace: String(h.workspace ?? ""), ...(h.workspace_name ? { name: h.workspace_name } : {}),
           path: String(h.path ?? ""), readers: (h.readers ?? []).map(String), sources: (h.sources ?? []).map(String) };
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
  | { role: "user"; text: string; user_text?: string; at?: number;
      /** the files the message carried (agent-api `shared/attachments.py`), never in `text` */
      attachments?: { name: string; path: string; type?: string; size?: number; kind?: "image" | "file" }[];
      /** set when another agent sent this turn (ADR-0046 decision 13) */
      from_agent?: { uid?: string; thread?: string; label?: string } }
  | { role: "agent"; text: string; ops?: { label: string; file?: string; wrote?: boolean; skill?: string; outcome?: string }[]; commit?: string; at?: number };
/** Why a turn ran, when the history says (a signal-woken turn: `"signal"`). */
type HistoryTrigger = { trigger?: string };

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
/** Chats whose next history read replaces the turns shown: another participant's turn landed. */
const rereadKeys = new Set<string>();
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
  const next = fn(getChatState(key));
  chatSessions.set(key, next);
  setChatActivity(key, next);
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
const OP_VERB: Record<string, string> = { read: "Reading", search: "Searching", edit: "Writing", git: "Committing", web: "Browsing", tool: "Working", skill: "Loaded skill" };
export function historyOp(op: { label: string; file?: string; wrote?: boolean; skill?: string; outcome?: string }): Op {
  // `skill`: a loaded skill (agent-api `workspace_reader.parse_history`) — the harness injected its
  // body as a user-role line, and it is a step of the agent's turn, never the person's words.
  const name = op.file ? op.file.split("/").filter(Boolean).pop() : op.label === "skill" ? op.skill : undefined;
  const verb = OP_VERB[op.label] ?? op.label;
  // `outcome`: how the call ended when it did not simply work — a held write reads held after a
  // reload too (held-label-rooms-1018)
  const status = settledStatus(op.outcome, true) ?? "done";
  return { icon: opIcon[op.label] ?? opIcon.tool, label: name ? `${verb} · ${name}` : verb, status, file: op.file, wrote: op.wrote };
}

type ReferenceToken = { kind: "file" | "meeting"; value: string; raw: string };
type ReferenceSegment = { kind: "text"; text: string } | { kind: "reference"; ref: ReferenceToken };
type ActiveReference = ReferenceToken;
const REFERENCE_RE = /@(file|meeting):([A-Za-z0-9._~%+@:/=-]+)/g;
const MAX_TEXTAREA_HEIGHT = 156;

function resizeComposerTextarea(el: HTMLTextAreaElement) {
  el.style.height = "auto";
  const height = Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT);
  el.style.height = `${height}px`;
  el.style.overflowY = el.scrollHeight > MAX_TEXTAREA_HEIGHT ? "auto" : "hidden";
}

/** A MESSAGE'S FILES RIDE ONE MARK, NOT A SENTENCE (founder, 2026-10-13: a pasted screenshot showed
 *  in his own bubble as "Attached files: 9caa6e9e467d2341-Screenshot_….png", and the agent then had to
 *  Glob for it). The mark goes after the person's words; agent-api vouches for each file, the worker
 *  hands the agent its exact path (and the picture itself, where the model takes images), and history
 *  returns the files as their own field. The bubble draws thumbnails; the mark is never shown. */
export function attachmentPrompt(prompt: string, files: UploadedWorkspaceFile[]): string {
  const mark = attachmentsMark(files.map((f) => ({ path: f.path, name: f.original || originalName(f.path) })));
  if (!mark) return prompt.trim();
  return prompt.trim() ? `${prompt.trim()}\n\n${mark}` : mark;
}

/** The bubble's attachments for files this browser just uploaded. */
export function sentAttachments(session: string, files: UploadedWorkspaceFile[]): MessageAttachment[] {
  return files.map((f) => ({
    name: f.original || originalName(f.path), path: f.path, src: attachmentSrc(session, f.path),
    size: f.size, type: f.type, kind: (f.type ?? "").startsWith("image/") || f.path.startsWith("assets/") ? "image" : "file",
  }));
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

/** A reference written in the text (`@file:…`, a meeting): static, so a Tag with its type icon —
 *  not a coloured pill (guidelines §4.5). The raw token is its tooltip. */
function ReferenceChip({ refToken }: { refToken: ReferenceToken }) {
  const isFile = refToken.kind === "file";
  const label = isFile ? fileLabel(refToken.value) : refToken.value;
  return (
    <span title={refToken.raw} className="vx-tag vx-ref" data-kind={isFile ? "file" : "meeting"}>
      <Icon name={isFile ? "file" : "cal"} size={12} />
      <span className="vx-ref-label">{label}</span>
    </span>
  );
}

function ReferenceText({ text }: { text: string }) {
  return <>{tokenizeReferences(text).map((part, i) => part.kind === "text"
    // a universal reference the person pasted or typed (#1783) is a chip the server resolves
    ? <span key={i}>{splitRefs(part.text).map((p, j) => p.kind === "text" ? p.text : <RefChip key={j} text={p.text} />)}</span>
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


/** A TURN ANOTHER AGENT SENT (ADR-0046 decision 13) — never drawn as the person's own bubble: on
 *  the agent's side, outlined, under who sent it, with the way to the thread it came from. */
export function AgentMessageBubble({ from, text }: { from: FromAgent; text: string }) {
  return (
    <div data-agent-message={from.thread} style={{ marginBottom: 16 }}>
      <div style={{ maxWidth: "82%", border: "1px dashed var(--line2, var(--line))", borderRadius: 12,
        borderTopLeftRadius: 4, padding: "8px 12px", fontSize: 13, color: "var(--t1)", lineHeight: 1.5,
        whiteSpace: "pre-wrap" }}>
        <div style={{ fontSize: 11.5, color: "var(--t3)", marginBottom: 4 }}>
          From {from.label || "another agent"} ·{" "}
          <button type="button" data-open-thread={from.thread}
            onClick={() => window.dispatchEvent(new CustomEvent(OPEN_CHAT_EVENT, { detail: { chat: from.thread } }))}
            style={{ background: "transparent", border: "none", padding: 0, color: "var(--accent)", cursor: "pointer", font: "inherit" }}>
            open its thread
          </button>
        </div>
        <ReferenceText text={text} />
      </div>
    </div>
  );
}

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

/** A job whose stream died with it unaccounted for. One spelling: the chat says it in a turn and the
 *  control that was pressed says it in place (Vexa-ai/vexa#1604), and they must not disagree. */
const LOST_JOB_LINE = "Lost the connection to that background job";

/** WHAT A BACKGROUND JOB IS DOING — in the chat, beside the step rows, never in the composer
 *  (Vexa-ai/vexa#1587). A job is not `busy` (Vexa-ai/vexa#1584): the composer stays free while it
 *  runs, which is the whole point — so its line cannot hang off a turn, and sits at the foot of the
 *  transcript instead. `jobLine` is the same shape the turn's own step row reads in, so nobody has
 *  to learn a second vocabulary; the job's TARGET is what tells two of them apart. */
function JobRows({ jobs: all, onRetry, onSendNow }: { jobs: JobRec[]; onRetry?: (j: JobRec) => void; onSendNow?: (j: JobRec) => void }) {
  // a queued MESSAGE is drawn as the person's pending bubble (`QueuedActions`), not as a line here
  const jobs = all.filter((j) => !isPendingMessage(j));
  if (jobs.length === 0) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, margin: "0 0 14px 5px" }}>
      {jobs.map((j) => (
        // A BLOCKED ROW IS NOT A WAITING ONE (P18). No spinner — nothing is coming — but the fault
        // that stopped it and the press that sends it again. In the row's own muted colour: red is
        // for a security refusal only (`faultTone`, founder 2026-10-13), never for "not right now".
        <div key={j.id} data-job-line data-job-blocked={j.blocked ? j.blocked.kind : undefined} title={jobLine([j])}
          role={j.blocked ? "alert" : undefined}
          style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, fontFamily: "var(--mono)", fontSize: 11.5, lineHeight: 1.5, color: j.blocked && faultTone(j.blocked) === "danger" ? "var(--danger)" : "var(--t2)" }}>
          {j.blocked
            ? <Icon name="x" size={12} style={{ flex: "none" }} />
            : <span className="vx-op-spin" style={{ width: 11, height: 11, borderRadius: "50%", border: "1.5px solid var(--line2)", borderTopColor: "var(--accent)", flex: "none" }} />}
          <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{jobLine([j])}</span>
          {j.blocked && onRetry && canRetry(j) && (
            <button data-job-retry onClick={() => onRetry(j)}
              style={{ flex: "none", fontSize: 11, fontFamily: "var(--mono)", color: "var(--blue)", background: "var(--bluebg)", border: "none", borderRadius: 6, padding: "2px 9px", cursor: "pointer" }}>
              Retry
            </button>
          )}
          {onSendNow && canSendNow(j) && (
            <button type="button" data-job-send-now onClick={() => onSendNow(j)} title="Stop the current answer and run this now"
              className="aw-fault-retry">
              Send now
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

/** Can a blocked row be sent again from here? A submission this browser still holds can — exactly
 *  as it was. A server-held MESSAGE can — its words are on the row. A server-held ACT cannot: its
 *  target and intent are not on the row, so it says what blocked it and the act is pressed again. */
function canRetry(j: JobRec): boolean {
  return !!j.outbox || (j.kind === "message" && !!j.display);
}

/** A routine's wake that found nothing new: one line, "Checked — …" (the delivery rule asks for it). */
export function isQuietCheck(text: string): boolean {
  const t = (text || "").trim();
  return /^Checked\s+[—-]/.test(t) && !t.includes("\n") && t.length <= 160;
}

/** A QUEUED MESSAGE IS THE PERSON'S OWN PENDING BUBBLE (#1783; founder liked Send now, not the
 *  element): right-aligned like a sent message, dimmed, the whole text, a small "Queued" label, and
 *  three pills on one line — Send now (stops the answer running now and runs this next), Edit (takes
 *  it back into the box), Remove. When the agent takes it, the label and pills go and the same bubble
 *  stays where it is. */
export const isPendingMessage = (j: JobRec): boolean =>
  !!j.queued && !!j.inbox && !j.blocked && (j.kind || "message") === "message";

function QueuedActions({ j, onSendNow, onEdit, onRemove }: {
  j: JobRec; onSendNow?: (j: JobRec) => Promise<boolean> | void; onEdit?: (j: JobRec) => void; onRemove?: (j: JobRec) => void;
}) {
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <div className="vx-queued-meta" data-queued-row={j.id}>
      <span className="vx-queued-label" role="status">
        <span className="vx-op-spin vx-queued-spin" aria-hidden />
        {sending ? "Sending now — stopping the current reply…" : "Queued · after the current reply"}
      </span>
      {failed && <span role="alert" className="vx-queued-err">Couldn't send it now. Try again.</span>}
      <span className="vx-queued-pills">
        {onSendNow && <button type="button" className="vx-pill" data-job-send-now disabled={sending}
          title="Stop the current reply and run this now"
          onClick={async () => { setSending(true); setFailed(false); const ok = await onSendNow(j); if (ok === false) { setSending(false); setFailed(true); } }}>Send now</button>}
        {onEdit && <button type="button" className="vx-pill" data-queued-edit disabled={sending} onClick={() => onEdit(j)}>Edit</button>}
        {onRemove && <button type="button" className="vx-pill" data-queued-remove disabled={sending} onClick={() => onRemove(j)}>Remove</button>}
      </span>
    </div>
  );
}

export function ChatConversation({ turns, busy, empty, jobs = [], room, onContinue, onStopRunning, onDecide, onRetryRow, onSendNow, onEditQueued, onRemoveQueued }: {
  turns: Turn[]; busy?: boolean; empty?: ReactNode; jobs?: JobRec[];
  /** A SHARED CHAT'S LINES — what people typed to each other — drawn in the same stream, by time. */
  room?: RoomView;
  /** A STOPPED TURN'S CONTINUE PRESS (Vexa-ai/vexa#1622) — forwarded to the bubble that draws it. */
  onContinue?: (turn: Extract<Turn, { role: "agent" }>) => void;
  /** "STOP IT" ON A TURN REFUSED BECAUSE A PREVIOUS REPLY IS STILL RUNNING — forwarded likewise. */
  onStopRunning?: (turn: Extract<Turn, { role: "agent" }>) => Promise<boolean>;
  /** the person's answer to an agent's write-target proposal (UI decision 6b) */
  onDecide?: (turn: Extract<Turn, { role: "agent" }>, choice: ProposalChoice) => void;
  /** A BLOCKED ROW'S RETRY PRESS (P18) — forwarded to the row that draws it. */
  onRetryRow?: (j: JobRec) => void;
  /** A QUEUED ROW'S "SEND NOW" — stop the running turn and run this message next. */
  onSendNow?: (j: JobRec) => Promise<boolean> | void;
  onEditQueued?: (j: JobRec) => void;
  onRemoveQueued?: (j: JobRec) => void;
}) {
  if (turns.length === 0 && empty && jobs.length === 0 && !room?.lines.length) return <>{empty}</>;
  // a queued message drawn ON its own bubble when the bubble is here; on a bubble of its own when
  // it is not (sent from another window, or this one reloaded)
  const pending = jobs.filter(isPendingMessage);
  const onTurn = new Set(turns.filter((t) => t.role === "user").map((t) => t.id));
  const actions = (j: JobRec) => <QueuedActions j={j} onSendNow={onSendNow} onEdit={onEditQueued} onRemove={onRemoveQueued} />;
  return (
    <>
      {groupRepeats(interleave(turns, room?.lines ?? [])).map((x) => x.kind === "repeat"
        ? <RepeatLine key={`r-${x.lines[0].seq}`} lines={x.lines} who={roomWho(room!, x.lines[0].author)} />
        : x.kind === "line"
        ? <PersonLine key={`l-${x.line.seq}`} seq={x.line.seq} who={roomWho(room!, x.line.author)} mine={x.line.author === room!.you}>
            <ReferenceText text={shownWords(x.line.text)} />
          </PersonLine>
        : ((t: Turn, i: number) => t.role === "user"
        ? (t.fromAgent
          ? <AgentMessageBubble key={t.id} from={t.fromAgent} text={t.text} />
          : (() => {
              const q = pending.find((j) => j.id === t.id);
              // THE PERSON'S MESSAGE IS A BUBBLE ON THE RIGHT (founder 2026-10-15: "user messages and
              // agent messages must be distinct"): a soft rounded bubble on a muted surface, no label
              // to read; the agent's answer is plain full-width text. Supersedes the named turns of
              // 2026-10-14. The name stays for screen readers.
              return <div key={t.id} data-turn="user" data-queued={q ? "" : undefined} className={q ? "vx-turn vx-turn-mine vx-queued" : "vx-turn vx-turn-mine"}>
                <div data-turn-who="" className="vx-sr">You</div>
                {t.attachments?.length ? <MessageAttachments items={t.attachments} /> : null}
                {t.text ? <div className="vx-bubble vx-bubble-mine"><UserMessage text={shownWords(t.text)} renderText={(x) => <ReferenceText text={x} />} /></div> : null}
                {q && actions(q)}
              </div>;
            })())
        // No report control on a turn (founder 2026-10-11: "remove - they report friction via
        // chat"). A person says what did not work to the agent, which files it with `report_friction`.
        // A ROUTINE'S QUIET WAKE: nothing changed, so it says one line starting "Checked —" and the
        // chat draws it as a muted tick, not a message — what makes a standing concern bearable.
        : isQuietCheck(t.text) && !(busy && i === turns.length - 1)
        ? <div key={t.id} data-quiet-check className="vx-quiet-check">✓ {t.text.trim()}</div>
        : <div key={t.id} data-turn="agent" data-from-meeting={t.role === "agent" && t.fromMeeting ? "" : undefined} className="vx-turn vx-turn-agent">
            <div data-turn-who="" className="vx-sr">Vexa</div>
            {t.role === "agent" && t.fromMeeting && <div data-from-meeting-marker="" className="vx-from-meeting"><Radio size={12} strokeWidth={1.75} aria-hidden />{wakeLabel(t)}</div>}
            <Conversation turns={[t]} busy={!!busy && i === turns.length - 1} onContinue={onContinue} onStopRunning={onStopRunning} onDecide={onDecide} />
          </div>)(x.turn, x.i))}
      {pending.filter((j) => !onTurn.has(j.id)).map((j) => (
        <div key={`q-${j.id}`} data-queued="" className="vx-turn vx-turn-mine vx-queued vx-queued-alone">
          <div data-turn-who="" className="vx-sr">You</div>
          <div className="vx-bubble vx-bubble-mine"><UserMessage text={j.text || j.target} renderText={(x) => <ReferenceText text={x} />} /></div>
          {actions(j)}
        </div>
      ))}
      <JobRows jobs={jobs} onRetry={onRetryRow} onSendNow={onSendNow} />
      {room && <RoomTurnStatus turn={room.turn} step={room.step} />}
    </>
  );
}

function ComposerReferences({ text }: { text: string }) {
  const refs = referenceTokens(text);
  if (refs.length === 0) return null;
  return (
    <div className="vx-refs">
      {refs.map((r) => <ReferenceChip key={`${r.kind}:${r.value}`} refToken={r} />)}
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
// A phase is a coloured DOT beside its word (never colour alone): live is success, recap is the
// meeting colour, preparing is neutral — accent is reserved for "this is the one / do this".
const MODE_CHIP: Record<MeetingPhase, { label: string; tone: "neutral" | "success" | "meeting" }> = {
  prep: { label: "Preparing", tone: "neutral" },
  live: { label: "In meeting", tone: "success" },
  post: { label: "Recap", tone: "meeting" },
};
const MODE_PLACEHOLDER: Record<MeetingPhase, string> = {
  prep: "Ask me to build the agenda, research attendees, or draft the brief…",
  live: "Ask about what's being said…",
  post: "Ask for the recap, decisions, or follow-up drafts…",
};
const meetingLabel = (m: MeetingMock) => m.title_custom || m.title;

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

/** The turn's prompt with the terminal's own narration of what the reader has open in front of it.
 *  Exported for the test that pins the boundary marker — see . */
export function promptWithActiveContext(prompt: string, ref: ActiveReference | null, meeting: MeetingMock | undefined): string {
  const context = activeContextPrompt(ref, meeting);
  // …AND THE SENTINEL GOES BETWEEN THEM (Vexa-ai/vexa#1603), for the same reason it does in
  // `promptWithReferences` above and with a sharper symptom. The server marks the boundary between
  // ITS grounding and `body.prompt` — but `body.prompt` is this whole composed string, so the
  // narration this composer wrote landed on the HUMAN side of the only boundary marker there was.
  // The worker records that side verbatim as `user_text` (`worker/engine.py::human_half`, which
  // cuts at the LAST sentinel), so the founder's own bubble read *"Active context: the user is
  // viewing the workspace file README.md. Read it with your Read tool if relevant. --- "* and then
  // his sentence. One marker, in the right place, and the human half is exact rather than
  // approximately right — the model still reads the narration first and the ask last, with an HTML
  // comment between them, so nothing about the turn it runs changes.
  return context ? `${context}\n\n---\n${CONTEXT_SENTINEL}${prompt.trim()}` : prompt.trim();
}

const ROUTINE_COMMAND = "/routine";
function isRoutineCommand(text: string): boolean {
  return /^\/routine(?:\s|$)/i.test(text);
}

function routineDescription(text: string): string {
  return text.replace(/^\/routine(?:\s+|$)/i, "").trim();
}

/** THE `/routine` ASK (ADR-0046): a routine is the person's own flow, made with the flows tool — never
 *  a file. Exported for the test that holds it to that. */
export function routineCreationPrompt(commandText: string): string {
  const description = routineDescription(commandText);
  if (!description) {
    return [
      "The user invoked /routine without a routine description.",
      "Ask one concise follow-up for the task to run and the cadence. Do not create the routine until the user gives enough detail, or explicitly accepts a default daily 9 AM schedule.",
    ].join("\n\n");
  }
  return [
    `Create a scheduled routine from this user request: ${JSON.stringify(description)}.`,
    "",
    "A routine is the person's own flow. Create it with the flows_submit tool — do not write any file for it:",
    '- name: a short kebab-case name for the task',
    '- trigger: {"kind": "schedule", "cron": "<5-field cron, UTC>"}',
    '- steps: ["agent_run"]',
    "- params.instruction: the recurring task itself, with the schedule wording removed",
    "- params.thread: this chat's thread id (stated at the top of your turn) — the routine runs in this chat",
    "Do not pass a scope: it is the person's own, taken from their credential.",
    "",
    "Derive the cron from the user's schedule words, converted to UTC. Examples: \"every 2 hours\" => \"0 */2 * * *\"; \"at 9am\" => \"0 9 * * *\" in the person's time zone, shifted to UTC. If no schedule is explicit, use daily at 9 AM. A cron faster than the allowed minimum interval is refused (quota_min_interval): say so and offer the nearest allowed one.",
    "The routine runs in this chat: every run continues this conversation and its alerts land here.",
    "When flows_submit answers, confirm in one line: the routine's name, when it runs, and that it runs in this chat.",
  ].join("\n");
}

/** `emptyExtra` — whatever the host wants in the void an empty conversation leaves between its
 *  greeting and the composer. The chat owns that layout and nothing else: it never decides what
 *  goes there (the minutes shell derives its proposal chips), so the two stay independent. */
type ChatProps = Partial<TabProps> & {
  emptyExtra?: ReactNode;
};


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
export function minutesEmptyGreeting(session: string): string {
  if (!session.startsWith("meet-")) return "";
  const held = liveMeetingsNow().some((mm) => session === `meet-${mm.id}` && ["completed", "stopped", "failed"].includes(String((mm as { live_status?: string }).live_status)));
  return (held ? MINUTES_ONBOARDING_GREETING : MINUTES_PREP_GREETING).replace("👋 ", "");
}

/** The meeting's greeting is the AGENT speaking: an agent line, left in the conversation like any
 *  turn, never centred grey text. Nothing at all in a room that is not a meeting's. */
export function MeetingGreeting({ session }: { session: string }) {
  const text = minutesEmptyGreeting(session);
  return text ? <AgentLine text={text} /> : null;
}

/** Everything a send carries besides its words.
 *
 *  `queuedRowId` USED TO BE HERE (Vexa-ai/vexa#1594) — the id of the "queued behind the current
 *  turn" row a press had already put on screen, handed to the send that eventually fired it. There
 *  is no such send any more: a mid-turn press goes straight to the server's inbox
 *  (Vexa-ai/vexa#1610) and the row is retired by whichever of the two things that own it happens
 *  first — the server dropping it from the pending list, or `job-started` naming its target. */
type SendOpts = {
  hidden?: boolean;
  ground?: boolean;
  scaffoldId?: string;
  intent?: ChatIntent;
  /** WATCH SOMETHING ALREADY SUBMITTED (Vexa-ai/vexa#1610) — the cursor to attach from. There is no
   *  prompt and no user bubble on this path: the words went to the server's inbox when the person
   *  submitted them, and this send exists only to render the turn they are about to get. Sending
   *  them again is the one thing that must not happen. */
  attachFrom?: string;
  /** The files this message carries, for its own bubble (the words for the agent carry the mark). */
  attachments?: MessageAttachment[];
  /** ADR-0048 — the account ids this message @-mentions (the picker's), and whether it is the
   *  agent's by construction (the first message of a chat that just became shared). */
  mentions?: string[];
  toAgent?: boolean;
};

/** A message waiting for the chat it was shared into to open (ADR-0048): pressing "Share and send"
 *  on a private chat starts a NEW shared chat, and the message is sent there once it is the open one. */
const pendingFirstSend = new Map<string, { text: string; mentions: string[] }>();

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
  // THE PANEL LISTS WHAT THIS CHAT'S AGENT WROTE (founder, 2026-10-15). The turns are this
  // surface's; the list is the shell's — so the writes are re-emitted (the shell reads them with `writesOf`), never listed here. A live
  // turn has no stored time yet, so it takes the moment it was first seen.
  const firstSeen = useRef(new Map<string, number>());
  useEffect(() => {
    const seen = (id: string) => firstSeen.current.get(id) ?? (firstSeen.current.set(id, Date.now()), Date.now());
    const wrote = turns.flatMap((t) => (t.role === "agent"
      ? [{ role: "agent", at: (t as { at?: number }).at ?? seen(t.id),
          ops: (t.ops ?? []).filter((o) => o.wrote && o.file).map((o) => ({ file: o.file, wrote: true })) }]
      : [])).filter((t) => t.ops.length > 0);
    window.dispatchEvent(new CustomEvent(CHAT_ARTIFACTS_EVENT, { detail: { session, turns: wrote } }));
  }, [turns, session]);
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
  const micDictRef = useRef<ComposerDictation | null>(null);   // the box's dictation, while the mic streams
  // LONG PASTES, COLLAPSED (`pastedText.tsx`): each one a chip above the box until the message goes.
  const [pastes, setPastes] = useState<string[]>([]);
  // S7: a pasted meeting link offers "Send Vexa" (7gg / 7t)
  const [meetingOffer, setMeetingOffer] = useState<ReturnType<typeof meetingLinkIn>>(null);
  // Every send path clears the box through here; a send while the mic streams also hands the sent
  // text over, so it is not shown again (see micDictRef).
  const clearInput = () => { setValue(""); setPastes([]); setMeetingOffer(null); micDictRef.current?.sent(); };
  // ADR-0048 — @-mentions: who the picker may offer, who was picked into this message, where the
  // caret is (the picker reads the `@query` before it), and the one question Enter may ask.
  const [mentionPeople, setMentionPeople] = useState<{ people: MentionablePerson[]; agents: { name: string }[] } | null>(null);
  const [picked, setPicked] = useState<Mention[]>([]);
  const [caret, setCaret] = useState(0);
  const [pickIdx, setPickIdx] = useState(0);
  const [pickClosed, setPickClosed] = useState(false);
  // FEATURE SWITCHES (#1783): with `shared_chats` off the @ picker offers nobody (adding a person
  // is the sharing act); with `folder_drop` off a dropped folder is not offered as a workspace.
  const sharingOn = useFeature("shared_chats");
  // ROOMS (R1): chats are private — @ tags and invites only in a room (the server refuses adding
  // people to a chat with 409 chats_are_private)
  const roomsOn = useFeature("rooms");
  const thisRoom = useRoom(session, roomsOn);
  const mentionOn = sharingOn && (!roomsOn || !!thisRoom?.room);
  const folderDropOn = useFeature("folder_drop");
  // HOSTED PLANS PICK A LEVEL 1–5 at the chat's start, not a model (`levels`; off: the full picker)
  const levelsOn = useFeature("levels");
  const [confirm, setConfirm] = useState<{ check: ShareCheck; text: string; busy: boolean; error?: string } | null>(null);
  // THE TAG CARDS (0.13.4 S4, D21): in a room, a mention of someone outside it posts the message and
  // shows the tagger — only the tagger — one card per person (rooms/TagCard.tsx)
  const [tagCards, setTagCards] = useState<TagAnswer[]>([]);
  useEffect(() => { setTagCards([]); }, [session]);
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  // The model picker's effort levels, offered in the "⋯" menu when the composer is narrow.
  const [effortItems, setEffortItems] = useState<MenuItem[] | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  // A meeting bundle handed to the chat is a call to import, not a file to attach (ChatBundleImport).
  const bundleImports = useBundleImports();
  // A FOLDER dropped on the chat becomes a workspace (founder 2026-10-13), offered first INTO the
  // workspace this chat writes to when that is one (`into: null` — the shell's host knows the target).
  const folderCards = useFolderImportedCards();
  const scrollRef = useRef<HTMLDivElement>(null);
  // Follow the stream ONLY while the reader is at the bottom — "chat should not fight with me when
  // it's outputting text and i scroll up to scroll chat history" (founder, Vexa-ai/vexa#1599).
  // The rule lives in surfaces/stickToBottom.ts, which decides from the container's POSITION: the
  // flag this used to keep was written by the `scroll` event, which is dispatched a frame late and
  // so still said at-the-bottom while the reader was already moving away from it.
  const [following, setFollowing] = useState(true);
  const stickRef = useRef<StickToBottom | null>(null);
  if (!stickRef.current) stickRef.current = createStickToBottom(() => scrollRef.current, { onFollowingChange: setFollowing });
  const stick = stickRef.current;
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => stick.onScroll();
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [stick]);
  // The transcript is measured AFTER the commit that grew it, which the stream callback below
  // cannot do — a chunk's text is not in the DOM yet when it fires. Keyed on the content itself
  // (every chunk rebuilds `turns`) so that typing in the composer costs no layout read.
  useEffect(() => { stick.onContent(); }, [stick, turns, chatState.jobs, busy, loading, bundleImports.cards]);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const attachmentSeqRef = useRef(0);
  const attachmentsRef = useRef<ComposerAttachment[]>([]);
  const uploadAbortRef = useRef(new Map<string, AbortController>());
  const uploadPromiseRef = useRef(new Map<string, Promise<UploadedWorkspaceFile | null>>());
  const [preview, setPreview] = useState<ComposerAttachment | null>(null);
  // ── mic dictation — STREAMING, meeting-pipeline style (sliding window + LocalAgreement
  //    via ui-kit/micDictation): confirmed + pending text land in the composer LIVE while
  //    speaking; stop flushes the final window. STT is proxied via /api/stt.
  const [mic, setMic] = useState<"idle" | "rec" | "stt">("idle");
  const [micError, setMicError] = useState<string | null>(null);
  const [micLevel, setMicLevel] = useState(0);
  const [micElapsed, setMicElapsed] = useState(0);
  useEffect(() => {
    if (mic !== "rec") return;
    const id = window.setInterval(() => setMicElapsed(Math.floor((Date.now() - micStartRef.current) / 1000)), 250);
    return () => window.clearInterval(id);
  }, [mic]);
  const micRef = useRef<StreamingDictation | null>(null);
  const micStartRef = useRef(0);
  useEffect(() => () => { micRef.current?.cancel(); }, []);  // release the mic on unmount
  const toggleMic = async () => {
    if (mic === "stt") return;
    if (mic === "rec") {
      const d = micRef.current;
      micRef.current = null;
      if (!d) { micDictRef.current = null; setMic("idle"); return; }
      if (Date.now() - micStartRef.current < 300) { d.cancel(); micDictRef.current = null; setMic("idle"); return; }  // accidental tap
      setMic("stt");
      try {
        const final = await d.stop();
        micDictRef.current?.finish(final);
        micDictRef.current = null;
        window.setTimeout(() => inputRef.current?.focus(), 0);
      } catch (e) {
        setMicError(e instanceof Error ? e.message : "Transcription failed");
        micDictRef.current = null;
      } finally { setMic("idle"); }
      return;
    }
    try {
      setMicError(null);
      const dict = createComposerDictation({ setValue });
      dict.start(value);
      micDictRef.current = dict;
      micStartRef.current = Date.now();
      setMicElapsed(0);
      micRef.current = await startStreamingDictation({
        onUpdate: dict.update,
        onLevel: setMicLevel,
        onError: () => { /* transient mid-stream faults are retried — stay quiet */ },
      });
      dict.attach(micRef.current);
      setMic("rec");
    } catch (e) { micDictRef.current = null; setMicError(micErrorMessage(e)); }
  };
  // Dictation shortcuts: Cmd/Ctrl+Shift+D starts or stops; Esc stops. The text stays in the box —
  // nothing is sent until Enter.
  const toggleMicRef = useRef(toggleMic);
  toggleMicRef.current = toggleMic;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === "d" || e.key === "D")) { e.preventDefault(); void toggleMicRef.current(); }
      else if (e.key === "Escape" && micRef.current) { e.preventDefault(); void toggleMicRef.current(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
    for (const c of uploadAbortRef.current.values()) c.abort();
  }, []);

  // ANOTHER PARTICIPANT'S TURN (#1783): when the conversation's version moves and this tab is not
  // itself mid-turn, the conversation is read again — the store holds every participant's turns.
  const [historyNonce, setHistoryNonce] = useState(0);
  const rereadHistory = useCallback(() => {
    const st = getChatState(chatKey);
    if (st.busy || st.loading) return;
    // the read REPLACES what is shown (it holds every turn shown now, and the other person's): an
    // idle chat that already had turns kept them and dropped the read (founder's call 2026-10-14)
    rereadKeys.add(chatKey);
    updateChatState(chatKey, (s) => (s.busy ? s : { ...s, loaded: false }));
    setHistoryNonce((x) => x + 1);
  }, [chatKey]);
  // A SHARED CHAT'S LINES are part of the conversation (drawn between the turns, by time); a move
  // of the conversation's version re-reads it.
  const room = useRoomLines(session, rereadHistory);
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
        const data: { turns?: (HistoryTurn & HistoryTrigger)[]; fault?: unknown } = await r.json();
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
        // A TURN THE MEETING WOKE (ADR transcript signals): its composed prompt is dropped and the
        // reply it produced is marked, so it reads as Vexa speaking up "from the meeting".
        const compacted = foldSignalWakes((data.turns ?? []).map((t) =>
          t.role === "user"
            ? { ...t, text: historyUserText(t), raw: t.text }
            : { ...t, raw: t.text }));
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
              ? { id: `h-u-${i}`, role: "user", text: t.text, ...(typeof t.at === "number" ? { at: t.at } : {}),
                  ...(t.attachments?.length ? { attachments: t.attachments.map((a) => ({
                    ...a, name: a.name || originalName(a.path), src: attachmentSrc(session, a.path) })) } : {}),
                  ...(t.from_agent?.thread ? { fromAgent: {
                    uid: String(t.from_agent.uid ?? ""), thread: String(t.from_agent.thread),
                    label: String(t.from_agent.label ?? "") } } : {}) }
              : { id: `h-a-${i}`, role: "agent", text: t.text, ops: (t.ops ?? []).map(historyOp), commit: t.commit, ...(typeof t.at === "number" ? { at: t.at } : {}),
                  ...(t.fromMeeting ? { fromMeeting: true, ...(t.wokeBy ? { wokeBy: t.wokeBy } : {}) } : {}) });
        // A REPLY THE STORE REFUSED IS SAID, NOT LOST (ADR-0048 S5): the history carries the typed
        // fault until the next save lands, and it renders as every typed fault does, at the end.
        const refused = readFault(data.fault);
        if (refused) loaded.push({ id: "h-fault", role: "agent", text: "", ops: [], failed: { fault: refused, retry: false } });
        const replace = rereadKeys.delete(key);
        updateChatState(key, (s) => {
          if (replace && !s.busy) return { ...s, turns: loaded, nextId: Math.max(s.nextId, loaded.length), loading: false, loaded: true };
          if (s.loaded || s.busy || s.turns.length > 0) return { ...s, loading: false, loaded: true };
          return { ...s, turns: loaded, nextId: Math.max(s.nextId, loaded.length), loading: false, loaded: true };
        });
      } catch {
        // A failed fetch must always clear `loading` (never hang) and leave `loaded` false so it retries.
        updateChatState(key, (s) => (s.loaded ? s : { ...s, loading: false }));
      }
    })();
  }, [chatKey, session, subject, historyNonce]);

  /** One upload per file, started the moment the file is offered — so the tray shows each file's
   *  own progress, and a send only waits for whatever is still in flight. The promise resolves to
   *  the stored file, or null when the upload failed or the file was removed. */
  const startUpload = (a: ComposerAttachment) => {
    const ctl = new AbortController();
    uploadAbortRef.current.set(a.id, ctl);
    const patch = (fields: Partial<ComposerAttachment>) =>
      setAttachments((current) => current.map((x) => (x.id === a.id ? { ...x, ...fields } : x)));
    const p = uploadAttachment(a.file, (progress) => patch({ progress }), ctl.signal, session)
      .then((uploaded) => { patch({ status: "done", progress: 1, uploaded, error: undefined }); return uploaded; })
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return null;
        patch({ status: "error", error: (e as Error)?.message || "Upload failed" });
        return null;
      })
      .finally(() => { if (uploadAbortRef.current.get(a.id) === ctl) uploadAbortRef.current.delete(a.id); });
    uploadPromiseRef.current.set(a.id, p);
  };

  // EVERY FILE THE CHAT IS HANDED comes through here — dropped, pasted or picked. Meeting bundles
  // leave for the import route first (their outcome is a card in the conversation); what is left is
  // attached exactly as before.
  const addFiles = (files: File[]) => {
    if (files.length === 0) return;
    const { now, check } = bundleImports.split(files);
    attachFiles(now);
    if (check.length > 0) void bundleImports.route(check).then(attachFiles);
  };

  const attachFiles = (files: File[]) => {
    if (files.length === 0) return;
    setUploadError(null);
    const added = files.map((file): ComposerAttachment => {
      const isImage = file.type.startsWith("image/");
      const refusal = refusalFor(file);
      return {
        id: `att-${attachmentSeqRef.current++}`,
        file,
        isImage,
        previewUrl: isImage && !refusal ? URL.createObjectURL(file) : undefined,
        status: refusal ? "refused" : "uploading",
        progress: 0,
        error: refusal ?? undefined,
      };
    });
    setAttachments((current) => [...current, ...added]);
    for (const a of added) if (a.status === "uploading") startUpload(a);
  };

  const retryAttachment = (id: string) => {
    const a = attachmentsRef.current.find((x) => x.id === id);
    if (!a || a.status !== "error") return;
    setUploadError(null);
    const again = { ...a, status: "uploading" as const, progress: 0, error: undefined };
    setAttachments((current) => current.map((x) => (x.id === id ? again : x)));
    startUpload(again);
  };

  const removeAttachment = (id: string) => {
    uploadAbortRef.current.get(id)?.abort();
    uploadAbortRef.current.delete(id);
    uploadPromiseRef.current.delete(id);
    setPreview((p) => (p?.id === id ? null : p));
    setAttachments((current) => current.filter((a) => {
      if (a.id !== id) return true;
      if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
      return false;
    }));
  };

  const clearAttachments = () => {
    uploadPromiseRef.current.clear();
    setAttachments((current) => {
      for (const a of current) {
        if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
      }
      return [];
    });
  };

  /** Everything in the tray, uploaded — or the reason the send cannot go yet. */
  const awaitAttachments = async (pending: ComposerAttachment[]): Promise<UploadedWorkspaceFile[] | string> => {
    const refused = pending.filter((a) => a.status === "refused");
    if (refused.length) return `Remove ${refused.map((a) => a.file.name || "the refused file").join(", ")} to send — it can't be attached.`;
    const results = await Promise.all(pending.map((a) => a.uploaded
      ? Promise.resolve(a.uploaded)
      : (uploadPromiseRef.current.get(a.id) ?? Promise.resolve(null))));
    // a file removed while the send waited is simply not sent — the person took it out
    const still = new Set(attachmentsRef.current.map((a) => a.id));
    const kept = pending.map((a, i) => ({ a, r: results[i] })).filter(({ a }) => still.has(a.id));
    const missing = kept.filter(({ r }) => !r).map(({ a }) => a);
    if (missing.length) return `${missing.map((a) => a.file.name || "A file").join(", ")} didn't upload — retry or remove it to send.`;
    return kept.map(({ r }) => r as UploadedWorkspaceFile);
  };

  // ── WHAT A TURN CARRIES, COMPOSED ONCE ──────────────────────────────────────────────────────
  //
  //  Two callers now: the send that STREAMS a turn, and the submit that only puts one on the
  //  server's inbox (Vexa-ai/vexa#1610). Two compositions of one turn is how a message queued at
  //  10:00 ends up grounded differently from the same message typed at 10:01 — the defect is
  //  invisible from either call site and obvious from between them, which is why this is a function.

  /** The prompt as it goes on the wire. `hidden` marks machinery in the RECORD, not only in this
   *  render: without the mark it comes back from `/api/sessions/<s>/history` on the next hydration
   *  as the person's own grey bubble. */
  const composePrompt = (prompt: string, referenceSource: string, o: SendOpts = {}) => {
    const raw = promptWithReferences(prompt, referenceSource.trim());
    const base = raw && o.hidden ? raw + MACHINERY_NOTE : raw;
    return { base, wire: (o.ground ?? true) ? promptWithActiveContext(base, contextRef, activeMeeting) : base };
  };

  /** The active center tab grounds the turn: a meeting passes {kind, platform, native_id, meeting_id}
   *  so agent-api folds its live transcript into the prompt server-side; a file passes {kind, ref}.
   *
   *  P0 (cross-tenant leak fix): `meeting_id` is the meetings-domain ROW id (the mock's `id`) — the
   *  transcript carrier keys on it, so grounding reads THIS row's transcript (`tc:meeting:{row_id}`),
   *  never a DIFFERENT tenant's / an older row's under the shared native. `native_id` is display only.
   *  The meeting's raw STATUS (+ title/when/workspace) rides along so agent-api branches the
   *  grounding by lifecycle phase — prep (no transcript, steer preparation) / live (fold the live
   *  stream) / post (fold the processed notes). A status-less payload keeps the legacy live path.
   *
   *  The CONTEXT BUNDLE (slice 1): tz + surface + focus + explicit include toggles. The focus
   *  mirrors `active` for meeting/file (server prefers `context`); workspace/today are new focus
   *  kinds the server folds itself. ground:false (onboarding) sends no bundle at all. */
  const wireContext = (ground: boolean) => {
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
    const wireFocus: FocusPayload | null | undefined = !ground
      ? undefined
      : (active && (active.kind === "meeting" || active.kind === "file"))
        ? (active as FocusPayload)
        : bundleFocus;
    const context = !ground ? undefined : buildChatContext({
      activeList, activeTab, focus: wireFocus ?? null, includeSchedule,
    });
    return { active, context };
  };

  // WHAT A STOPPED TURN WOULD RE-SUBMIT (Vexa-ai/vexa#1622) — per agent-turn id, the intent that
  // turn was running under and the instruction the harness offered to continue with.
  //
  // A REF BESIDE THE TURN, not a field on it. The Continue act has to go back to the SAME TARGET,
  // and the target lives on the intent — a chat concern that `surfaces/agent-window`'s `Turn`
  // (a rendering model, shared with the meeting copilot) has no business carrying. Nothing here is
  // rendered, so nothing here may cause a render either.
  const continuable = useRef(new Map<string, { instruction: string; intent?: ChatIntent;
    /** THE SAME WORDS, AGAIN (P18) — a turn a typed fault ended keeps what it was sent with, so its
     *  Retry re-sends exactly that turn rather than a continue instruction. */
    retry?: { display: string; hidden?: boolean; ground?: boolean; scaffoldId?: string } }>());

  const send = async (text: string, prompt = text, referenceSource = text, opts: SendOpts = {}) => {
    // hidden → no visible user bubble (system kickoffs); ground:false → don't append the active
    // meeting/file context (onboarding must not inherit whatever meeting happens to be focused).
    const { hidden = false, ground = true, attachFrom, mentions, toAgent } = opts;
    const v = text.trim();
    const files = opts.attachments ?? [];
    const { base: basePrompt, wire: p } = composePrompt(prompt, referenceSource, opts);
    const key = chatKey;
    const sessionForSend = session;
    const state = getChatState(key);
    // AN ATTACH CARRIES NO WORDS, by construction — they are already on the server (see `attachFrom`
    // on SendOpts). Everything else about this function is unchanged, which is the point: one
    // rendering path, whether the turn was dispatched here or is being watched.
    if ((!attachFrom && ((!v && files.length === 0) || !basePrompt)) || state.busy) {
      // NOTHING IS BEING SENT, so the CONTROL that was pressed must stop saying something is —
      // that is the same lie as a spinner that outlives its turn, one object along
      // (Vexa-ai/vexa#1604).
      if (opts.intent && isJobIntent(opts.intent)) actSettled(actTarget(opts.intent));
      return;
    }
    // …AND THIS ONE IS ON THE WIRE (Vexa-ai/vexa#1604). An act that waited for the turn in front of
    // it stops saying it is queued at the moment it stops being queued — a control that goes on
    // saying it is the same defect as one that says nothing, a minute later.
    if (opts.intent && isJobIntent(opts.intent)) actSending(actTarget(opts.intent));
    const n = state.nextId;
    // `let`: THE BUBBLE THE STREAM IS WRITING INTO MOVES when this view carries a second turn — the
    // answer to something the person submitted while this one was running (`onTurn` below). Every
    // patch reads it at call time, so routing is this one assignment.
    let agentId = `a-${n}`;
    const bubbles = new Map<string, string>();   // worker turn id → the agent bubble that renders it
    // WHOSE TURN IS STREAMING HERE (call-person-autojoin): the first turn of a view that DISPATCHED
    // the message is this person's; an attach, or a second turn on this view, may be anyone's.
    let dispatchedTurn = !attachFrom;
    const dispatchedTurns = new Set<string>();
    if (!attachFrom) lastLocalSubmitRef.current = Date.now();
    const mySeq = ++sendSeqRef.current;
    const displayText = advertiseFocus ? appendReferenceToken(v, contextRef) : v.trim();
    const ctrl = new AbortController();
    // AN ATTACH DRAWS NO USER BUBBLE for the same reason a hidden turn does not: the person's own
    // words are already on screen — they were painted at the moment they were submitted, dimmed,
    // and this turn is the answer to them.
    const newTurns = hidden || attachFrom
      ? [{ id: agentId, role: "agent" as const, text: "", ops: [] }]
      : [{ id: `u-${n}`, role: "user" as const, text: displayText, ...(files.length ? { attachments: files } : {}) },
         { id: agentId, role: "agent" as const, text: "", ops: [] }];
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
    const setStatus = (phase: ChatPhase | null, label?: string) =>
      patchAgentTurn(key, agentId, (t) => ({ ...t, status: phase ? { phase, since: t.status?.since ?? Date.now(), ...(label ? { label } : {}) } : null }));
    const { active, context } = wireContext(ground);
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
    openViewsRef.current += 1;
    try {
      const result = await streamChatTurn(
        // `scaffold_id` on the FIRST turn: dispatch reads the same record the panel rendered from.
        // `intent` (PRD decision 32) — an Extend/Create press is an ACT on a named file, not a
        // sentence; it travels typed so the server can turn it into a preset without parsing prose.
        { prompt: attachFrom ? "" : p, session: sessionForSend, active, context, scaffold_id: opts.scaffoldId, intent: opts.intent, mentions, to_agent: toAgent },
        {
          onStarting: () => {},  // visual is driven by onStatus (below); the stream still signals cold-start here
          onStatus: (phase, label) => setStatus(phase, label),
          // A TURN BEING TAKEN IS SOMETHING LEAVING THE INBOX (Vexa-ai/vexa#1610), so this is when
          // the queued rows are re-read: the row for the message now answering stops saying it is
          // waiting at the moment it stops waiting, rather than when the answer finishes.
          onAccepted: () => { void refreshPending(); },
          // A SHARED CHAT'S LINE, NOT A TURN (#1783): the message went to the people in the chat, and
          // the room's lines show it with the sender's name — so the bubble pair painted for a turn
          // goes, and the lines read now.
          onNote: () => {
            updateChatState(key, (s) => ({ ...s, turns: s.turns.filter((t) => t.id !== `u-${n}` && t.id !== agentId) }));
            if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(ROOM_LINES_EVENT));
          },
          // ONE VIEW, MORE THAN ONE TURN (the founder, 2026-10-10: *"sometimes chat does not answer
          // if asked while it's not yet answered"*). A message submitted while this turn ran is
          // taken by the worker while this turn is still writing its trailing steps, so its answer
          // streams HERE. It gets its own bubble, below the person's message — never folded into
          // the bubble above it, where it read as no answer at all. Only while this is the chat's
          // newest view: a turn sent from the composer after this one has its own connection, and
          // rendering it twice would be the opposite failure.
          onTurn: (turnId, isNew) => {
            const known = bubbles.get(turnId);
            if (known) { agentId = known; dispatchedTurn = dispatchedTurns.has(turnId); return true; }
            if (!isNew) { bubbles.set(turnId, agentId); if (dispatchedTurn) dispatchedTurns.add(turnId); return true; }
            if (sendSeqRef.current !== mySeq) return false;
            dispatchedTurn = false;
            const prev = agentId;
            patchAgentTurn(key, prev, (t) => ({ ...t, status: null }));
            const next = `a-${getChatState(key).nextId}`;
            updateChatState(key, (s) => ({
              ...s,
              turns: [...s.turns, { id: next, role: "agent" as const, text: "", ops: [] }],
              nextId: s.nextId + 1,
            }));
            bubbles.set(turnId, next);
            agentId = next;
            breakBeforeNextDelta = false;
            return true;
          },
          onDelta: (text) => patchAgentTurn(key, agentId, (t) => {
            const joined = joinInterim(t.text ?? "", text, breakBeforeNextDelta);
            breakBeforeNextDelta = false;
            return { ...t, status: null, text: joined };
          }),
          // A FILE THIS TURN WROTE. Re-emitted for the shell, which owns the chat record's tabs —
          // this surface never opens a document itself (F41).
          onArtifact: (a) => window.dispatchEvent(new CustomEvent(ARTIFACT_EVENT, { detail: a })),
          // …AND A PAGE THE PERSON ASKED FOR (Vexa-ai/vexa#1586). Its own event, not the artifact
          // one: an artifact defers to a reader who has opened something else, an open is that
          // reader. Same forwarding rule — this surface opens nothing itself.
          onOpen: (o) => window.dispatchEvent(new CustomEvent(OPEN_PAGE_EVENT, { detail: o })),
          onFocusWorkspace: (f) => window.dispatchEvent(new CustomEvent(FOCUS_WORKSPACE_EVENT, { detail: f })),
          // THE AGENT STARTED A CALL FOR THIS PERSON ("Call tester-b"). Only the view whose own
          // message placed it, and only live (history never streams events): the shell opens the
          // room and joins as the caller. Every other reader of the chat sees the reply and nothing
          // more. This surface joins nothing itself; the shell owns the call (`callPlaced.ts`).
          onCall: (c) => {
            if (!callFromThisView({ dispatchedTurn, lastLocalSubmitMs: lastLocalSubmitRef.current, now: Date.now() })) return;
            window.dispatchEvent(new CustomEvent(CALL_PLACED_EVENT, { detail: c }));
          },
          // THE AGENT ASKS TO MOVE THIS CHAT'S WRITES (UI decision 6b): the question goes on its
          // bubble with Switch / Keep. Nothing moves until the person clicks.
          onProposeTarget: (p) => patchAgentTurn(key, agentId, (t) => ({ ...t, proposal: p })),
          // TERMS THIS TURN PUBLISHED for a meeting's transcript (PRD decision 35). Same seam and
          // same reason as the artifact above: the chips are part of the chat's record and the
          // transcript renders that record — this surface forwards and stores nothing.
          onTerms: (t) => window.dispatchEvent(new CustomEvent(TERMS_EVENT, { detail: t })),
          // A sign-in browser the agent opened (`browser_session_open`): its result carries the
          // session, and the shell's live view shows it. Same forwarding rule as `onTool`.
          onToolResult: (tool, r) => {
            // A WRITE HELD FOR THE PERSON'S APPROVAL becomes a card, not a typed code (2026-10-16).
            // The server's `held` fields first: the result body is a short summary on most harnesses
            // and a cut-off JSON body made no card at all.
            const held = (r.held ? heldApproval(r.held) : null) ?? privateApproval(r.result);
            if (held) patchAgentTurn(key, agentId, (t) => ({ ...t, approval: held }));
            // …AND ITS STEP SAYS SO (held-label-rooms-1018): held, or not done, never "Wrote".
            const ended = settledStatus(r.outcome ?? (held ? "held" : undefined), r.ok);
            if (ended) patchAgentTurn(key, agentId, (t) => ({ ...t, ops: markOp(t.ops, r.callId, tool, ended) }));
            const ui = r.ok ? toolResultUiEvent(tool, r.result) : null;
            if (ui) window.dispatchEvent(new CustomEvent(ui.name, { detail: ui.detail }));
          },
          // FILES THE TURN WROTE THAT ITS COMMIT KEPT BACK (D6): their steps read held, and the
          // release is the same card a held tool write gets.
          onWithheld: (w) => patchAgentTurn(key, agentId, (t) => ({
            ...t, ops: markWithheld(t.ops, w.paths),
            ...(w.code && !t.approval ? { approval: { code: w.code, workspace: w.workspace, path: w.paths.join(", "),
                                                      readers: w.readers, sources: w.sources } } : {}),
          })),
          onTool: (tool, args, callId) => {
            const ui = toolUiEvent(tool);
            if (ui) window.dispatchEvent(new CustomEvent(ui, { detail: args }));
            breakBeforeNextDelta = true;      // F40 — the assistant message ended here
            const op = { ...toolOp(tool, args), ...(callId ? { callId } : {}) };
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
            // THE QUEUED ROW BECOMES THE RUNNING ONE (Vexa-ai/vexa#1594). The press already put a
            // line on screen; this is that line learning its job id. Two rows for one act would
            // read as two acts.
            updateChatState(key, (s) => ({
              ...s, busy: false,
              // ONE ACT, ONE ROW (Vexa-ai/vexa#1610). Two things can be true when a job starts, and
              // both retire a waiting row into a running one rather than leaving two on screen:
              // the act was QUEUED IN THE SERVER'S INBOX (`claimInboxRow` — the row this browser
              // drew at the press, or the one the pending list gave it), or it was queued BEHIND
              // ANOTHER JOB on the same page and already carries this id (`promoteJob`).
              // `startJob` adds nothing for an id it already has, which is what makes the pair safe.
              jobs: promoteJob(startJob(claimInboxRow(s.jobs, j.target),
                                        { id: j.jobId, kind: j.kind, target: j.target }), j.jobId),
            }));
            // …AND THE CONTROL THAT WAS PRESSED LEARNS THE SAME ID (Vexa-ai/vexa#1604). It has been
            // saying "Creating…" since the press; from here it can count the job's own steps.
            actStarted({ job: j.jobId, kind: j.kind, target: j.target });
          },
          // QUEUED BEHIND ANOTHER ACT ON THE SAME PAGE (Vexa-ai/vexa#1610) — where a refusal used to
          // be. The turn that asked is over, so the composer comes back exactly as it does for a job
          // that started; the row says it is waiting, and it already carries the id it will run
          // under. Never a dead end: the same connection sees it start, step and land.
          onJobQueued: (j) => {
            ownsBusy = false;
            startedJobs.add(j.jobId);
            patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, ops: settleOps(t.ops) }));
            updateChatState(key, (s) => ({
              ...s, busy: false,
              jobs: queueJob(claimInboxRow(s.jobs, j.target),
                             { id: j.jobId, kind: j.kind, target: j.target }),
            }));
            actQueued({ target: j.target, kind: j.kind });
          },
          onJobStep: (jobId, tool) => {
            const step = toolOp(tool).label.split(" · ").pop() ?? "";
            actStepped(jobId, step);
            updateChatState(key, (s) => ({ ...s, jobs: stepJob(s.jobs, jobId, step) }));
          },
          // STILL GOING, IN A FRESH WINDOW (Vexa-ai/vexa#1613). A long act that reached its
          // per-window tool-call budget used to die there — the founder's Example Bank job ran 72 steps
          // and then said it had failed. It now checkpoints and continues, and the row it already
          // has says so. No step is counted: nothing was done, something was RESUMED.
          onJobProgress: (jobId, line) => {
            actNoted(jobId, line);
            updateChatState(key, (s) => ({ ...s, jobs: noteJob(s.jobs, jobId, line) }));
          },
          // ONE LINE, ALWAYS — landed or died. A job that finishes in silence is indistinguishable
          // from one that is still running, and the chip has just gone.
          onJobEnd: ({ jobId, ok, line }) => {
            startedJobs.delete(jobId);
            // THE CONTROL LANDS OR SHOWS WHY (Vexa-ai/vexa#1604). Landed → its state goes and it is
            // a control again; the page it wrote is already on screen, put there by the commit.
            // Died → it keeps the one line and offers the act again.
            actEnded(jobId, ok, line);
            updateChatState(key, (s) => ({
              ...s,
              jobs: endJob(s.jobs, jobId),
              turns: line ? [...s.turns, { id: `j-${jobId}`, role: "agent" as const, text: line, ops: [] }] : s.turns,
            }));
          },
          onRejected: () => patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, rejected: "workspace.v1 violation — reverted" })),
          // ONE FAILURE SURFACE PER TURN. A server one release behind sends no `fault`, and its
          // failed reply is often the very text the turn already streamed (the CLI's `API Error:
          // 402 …` arrived as a delta, then again as the reply). Said once is enough: the failure
          // line does not repeat words already on screen.
          onModelFailure: (reply) => patchAgentTurn(key, agentId, (t) => {
            const said = (t.text ?? "").trim();
            const r = (reply ?? "").trim();
            const repeat = !!r && !!said && said.endsWith(r);
            return { ...t, status: null, text: (t.text ?? "") + (t.text ? "\n\n" : "") + `Model inference failed${r && !repeat ? `: ${r}` : "."}` };
          }),
          // A TYPED FAULT ENDED THE TURN (P18): the runtime would not start the agent, or the model
          // provider refused it. It renders as its own block — who failed, what kind, the detail,
          // the remedy — never as "Internal Server Error" or a provider's JSON in the reply. A turn
          // that carried words can be sent again from the block; an attach carried none.
          onFault: (fault) => {
            const retry = !attachFrom && !!v;
            if (retry) {
              continuable.current.set(agentId, { instruction: prompt, intent: opts.intent,
                retry: { display: text, hidden, ground, scaffoldId: opts.scaffoldId } });
            }
            patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, failed: { fault, retry } }));
          },
          // THE TURN STOPPED EARLY (F89) — not the same thing as the model failing. Keep whatever
          // the turn did produce and say plainly that it is partial, so the person knows to ask
          // again rather than reading half an answer as the whole one.
          //
          // …AND SAY IT AS A FIELD, NOT AS PROSE (Vexa-ai/vexa#1622). Appending `_reason_` to the
          // text was the F89 fix and it was not enough: the sentence read as part of the agent's
          // answer, and prose cannot carry a button. The line now renders as its own row under the
          // reply, with the Continue act beside it — which is the whole difference between "that
          // turn stopped" and the founder re-typing the same instruction three times.
          onTruncated: (reason, partial, stop) => {
            if (stop?.act) {
              continuable.current.set(agentId, { instruction: stop.act.instruction, intent: opts.intent });
            }
            patchAgentTurn(key, agentId, (t) => ({
              // `||`, not `??`: an agent turn is CREATED with `text: ""`, so the old nullish
              // fallback never once reached `partial` — on a backend that streams no deltas (or a
              // view that attached after they went past) the reply the turn did manage was
              // dropped, and the person got a stop line over an empty bubble.
              ...t, status: null, text: t.text || partial || "",
              ...(typeof stop?.steps === "number" ? { steps: stop.steps } : {}),
              stopped: { line: reason, ...(stop?.act ? { act: stop.act } : {}) },
            }));
          },
          // THE SERVER'S STEP COUNT (Vexa-ai/vexa#1622) — it replaces this browser's own tally in
          // the settled op line. A view that attached to a turn already in flight counted only what
          // it saw; the server counted the turn.
          onSteps: (steps) => patchAgentTurn(key, agentId, (t) => ({ ...t, steps })),
          onCompacted: (compacted) => patchAgentTurn(key, agentId, (t) => ({ ...t, compacted })),
          // agent-api sends one fetched page per frame; a turn's sources are their union by URL
          onSources: (items) => { const fresh = readSources(items); if (fresh.length) patchAgentTurn(key, agentId, (t) => ({ ...t, sources: mergeSources(t.sources ?? [], fresh) })); },
          onError: (msg) => patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, text: (t.text ?? "") + (t.text ? "\n\n" : "") + presentError(new Error(msg)).headline })),
          onProgress: () => stick.onContent(),
        },
        { signal: ctrl.signal, attachFrom },
      );
      // WHERE THIS CHAT HAS READ TO (Vexa-ai/vexa#1610) — what the NEXT queued item attaches from,
      // so watching a queue costs no gap and replays nothing. Remembered even on a failed turn: the
      // cursor is a position on the Stream, not a verdict about the turn.
      // Only ever FORWARD: a job's view can end after a later turn's view did, and moving the cursor
      // back would make the catch-up attach to a turn that was already watched.
      if (result.cursor && streamIdAfter(result.cursor, cursorRef.current)) cursorRef.current = result.cursor;
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
      openViewsRef.current -= 1;
      // whatever happened — abort, error, timeout — no step is left spinning. A spinner that
      // outlives its turn is the same lie as a tick that precedes it. Every bubble this view wrote.
      for (const id of new Set([agentId, ...bubbles.values()])) {
        patchAgentTurn(key, id, (t) => ({ ...t, status: null, ops: settleOps(t.ops) }));
      }
      if (ownsBusy) updateChatState(key, (s) => ({ ...s, busy: false, abort: null }));
      // A JOB CHIP MUST NOT OUTLIVE ITS CONNECTION, for the same reason a spinner must not outlive
      // its turn. `onJobEnd` removes each one as it lands, so anything still in this set means the
      // stream died with that job unaccounted for — say so, and stop spinning.
      for (const lost of startedJobs) {
        actEnded(lost, false, LOST_JOB_LINE);
        updateChatState(key, (s) => ({
          ...s, jobs: endJob(s.jobs, lost),
          turns: [...s.turns, { id: `j-${lost}`, role: "agent" as const, text: `_${LOST_JOB_LINE} — it may still have finished; reopen the page to see._`, ops: [] }],
        }));
      }
      // THE TURN IS OVER AND NO JOB EVER CLAIMED THIS ACT (Vexa-ai/vexa#1604) — a refusal, an error,
      // or a deployment whose worker still runs the act inline. Whatever happened, nothing is
      // running, so the control must not go on saying it is. A FAILURE is left standing: it is not
      // waiting for anything, it is telling the person something.
      if (opts.intent && isJobIntent(opts.intent)) actSettled(actTarget(opts.intent));
    }
  };

  // STOP ENDS THE TURN, NOT ONLY THIS TAB'S VIEW OF IT (app.dev 2026-10-15): aborting the fetch
  // left a fourteen-minute grep running on the worker, and the next message got a 409. The server is
  // asked to kill the turn and its tools; that turn's own stream then says it stopped.
  const stop = () => {
    void stopTurn(session);
    getChatState(chatKey).abort?.abort();
    updateChatState(chatKey, (s) => ({ ...s, busy: false, abort: null }));
  };

  // ── THE INBOX (Vexa-ai/vexa#1610) ───────────────────────────────────────────────────────────
  //
  //  `cursorRef` is where this chat has read to on its own output Stream; `attachedRef` is the
  //  queue head it last opened a view for. Refs and not state: neither is rendered, and neither may
  //  cause a render — an attach that re-fired on every keystroke would be a second reader of one
  //  turn.
  const cursorRef = useRef("");
  const attachedRef = useRef("");
  //  …and `catchingUp` is the one-at-a-time latch on the catch-up below. Without it a fast
  //  busy→idle→busy flicker can run two flushes over one outbox, and both would send it.
  const catchingUp = useRef(false);
  //  `openViewsRef` counts this chat's open connections (a job keeps its turn's open after `busy`
  //  is handed back), `sendSeqRef` numbers the sends so a view knows whether it is still the newest
  //  (`onTurn`), and `caughtUp` re-runs the catch-up once a mid-turn submission has been ACKED — the
  //  turn in front may have ended while the POST was in flight, and then nothing else would.
  const openViewsRef = useRef(0);
  const inFlightRef = useRef(new Set<string>());
  const rerunCatchUp = useRef(false);
  const sendSeqRef = useRef(0);
  //  When THIS TAB last put a message of this person's on the wire (a dispatch or an inbox submit):
  //  the one fact that lets a turn this view did not dispatch still count as theirs (`onCall`).
  const lastLocalSubmitRef = useRef(0);
  const [catchUpTick, setCatchUpTick] = useState(0);
  useEffect(() => { cursorRef.current = ""; attachedRef.current = ""; }, [session]);

  /** Read the server's pending list back and make the queued rows agree with it. The rows this
   *  chat DREW optimistically are replaced by the ones the server holds — same ids, so nothing
   *  flickers — and a row whose work has been taken disappears because the server stopped listing
   *  it, not because this browser decided it had. */
  const refreshPending = async (after = "") => {
    const view = await fetchPending(session, fetch, after);
    updateChatState(chatKey, (s) => ({ ...s, jobs: reconcileInbox(s.jobs, view.pending) }));
    return view;
  };

  /** Put a submission on the server NOW — the whole of what changed for a person typing mid-turn.
   *  On a refused or dropped POST the unsent copy stays in the outbox and the next idle moment
   *  retries it; the row stays on screen until the server confirms one way or the other, because a
   *  row that vanishes is indistinguishable from a message that was never sent. */
  const submitToServer = async (id: string, prompt: string, referenceSource: string, o: SendOpts) => {
    const { wire } = composePrompt(prompt, referenceSource, o);
    const { active, context } = wireContext(o.ground ?? true);
    // IN FLIGHT, NOT UNSENT: the outbox holds this copy until the ack, and a catch-up that ran in
    // between must not send it a second time (the worker may already be answering the first).
    inFlightRef.current.add(id);
    lastLocalSubmitRef.current = Date.now();
    try {
      const view = await submitToInbox({
        id, session, prompt: wire, active, context, scaffoldId: o.scaffoldId, intent: o.intent,
        display: referenceSource,
      });
      updateChatState(chatKey, (s) => ({ ...s, jobs: reconcileInbox(s.jobs, view.pending) }));
      // ON THE SERVER NOW — look again. The turn in front may have ended while this POST was in
      // flight, and the catch-up that ran then could not have seen it.
      setCatchUpTick((n) => n + 1);
    } catch (e) {
      // Left in the outbox on purpose — `flushOutbox` re-sends it the moment the chat is idle, and
      // again on the next load. Saying nothing is right for a NETWORK gap: the message is not lost,
      // and an error banner for a retry that is about to succeed is its own kind of lie.
      //
      // …BUT A TYPED REFUSAL IS NOT A GAP (P18). The server answered, and said which dependency
      // stopped it — the runtime that would not start the agent. Silence there is the founder's
      // "queued behind the current turn" forever, so the row says it is blocked, and by what.
      if (e instanceof SubmitRefused && e.fault) {
        const fault = e.fault;
        updateChatState(chatKey, (s) => ({ ...s, jobs: blockSubmission(s.jobs,
          { id, kind: o.intent?.kind, display: referenceSource }, fault) }));
      }
    } finally {
      inFlightRef.current.delete(id);
    }
  };

  /** SEND A BLOCKED ROW AGAIN (P18). A submission this browser still holds goes back exactly as it
   *  was, under the same id; a server-held message goes back with its own words under ITS id — and
   *  either way the server withdraws any copy it still holds under that id first, so it runs once. */
  const retryRow = async (j: JobRec) => {
    const held = readOutbox(session).find((x) => x.id === j.id);
    if (!held && !(j.kind === "message" && j.display)) return;
    updateChatState(chatKey, (s) => ({ ...s, jobs: s.jobs.filter((x) => x.id !== j.id || x.inbox) }));
    if (!held) { void submitToServer(j.id, j.display ?? "", j.display ?? "", {}); return; }
    try {
      const view = await submitToInbox(held);
      updateChatState(chatKey, (s) => ({ ...s, jobs: reconcileInbox(s.jobs, view.pending) }));
    } catch (e) {
      if (e instanceof SubmitRefused && e.fault) {
        const fault = e.fault;
        updateChatState(chatKey, (s) => ({ ...s, jobs: blockSubmission(s.jobs,
          { id: j.id, kind: held.intent?.kind, display: held.display ?? j.display ?? j.target }, fault) }));
      }
    }
  };

  // A canvas keyword chip (or any harness `actions.ask`) asks the visible chat a question: reveal the rail
  // and stream the answer here. sendRef keeps the latest `send` closure so the listener stays stable.
  const sendRef = useRef(send);
  sendRef.current = send;

  /** CONTINUE A TURN THAT RAN OUT OF BUDGET (Vexa-ai/vexa#1622).
   *
   *  It goes out through `ASK_CHAT_EVENT` — the one door every act already uses — so it inherits
   *  #1610's inbox for free: pressed while something else is running it is SUBMITTED to the server
   *  at once and drawn as a queued row; pressed on an idle chat it is a turn like any other. The
   *  submission never lives in this browser, which is the property that made re-pressing safe.
   *
   *  A page act carries its INTENT back with the new instruction, so the act lands on the same
   *  target and the worker queues it behind whatever is writing that page. A turn somebody typed
   *  has no intent, and its target is the conversation — which is where the words go anyway. */
  /** THE PERSON ANSWERS A WRITE-TARGET PROPOSAL (UI decision 6b). Switch moves this chat's target —
   *  through the shell, the one writer of it on this side, exactly as a chip click — and Keep moves
   *  nothing. Either way the bubble says what they chose and the agent, which is waiting, gets the
   *  answer as their next message. */
  const decideTarget = (t: Extract<Turn, { role: "agent" }>, choice: ProposalChoice) => {
    if (!t.proposal || t.proposal.decided) return;
    const d = decideProposal(t.proposal, choice, session);
    if (d.target) window.dispatchEvent(new CustomEvent(CHOOSE_TARGET_EVENT, { detail: d.target }));
    patchAgentTurn(chatKey, t.id, (x) => (x.proposal ? { ...x, proposal: { ...x.proposal, decided: choice } } : x));
    window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: d.ask }));
  };

  const continueTurn = (t: Extract<Turn, { role: "agent" }>) => {
    const held = continuable.current.get(t.id);
    // RETRY A TURN A FAULT ENDED (P18) — the same words, the same options, once per press: the
    // control goes as it is pressed, and a second failure draws its own block with its own Retry.
    if (t.failed && held?.retry) {
      continuable.current.delete(t.id);
      // …AND THE FAILED ATTEMPT LEAVES NO TRACE (founder, 2026-10-13: three copies of one message
      // and two red boxes). The retry draws the person's words again as it sends them, so the
      // attempt that never got through — its bubble and the fault under it — goes first: one
      // message on screen, and an answer under it when the retry lands.
      updateChatState(chatKey, (s) => {
        const at = s.turns.findIndex((x) => x.id === t.id);
        if (at < 0) return s;
        const from = at > 0 && s.turns[at - 1].role === "user" && !held.retry?.hidden ? at - 1 : at;
        return { ...s, turns: [...s.turns.slice(0, from), ...s.turns.slice(at + 1)] };
      });
      window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, {
        detail: { prompt: held.instruction, display: held.retry.display, hidden: held.retry.hidden,
                  ground: held.retry.ground, scaffoldId: held.retry.scaffoldId,
                  ...(held.intent ? { intent: held.intent } : {}) },
      }));
      return;
    }
    const instruction = t.stopped?.act?.instruction || held?.instruction || "";
    if (!instruction) return;
    continuable.current.delete(t.id);
    const intent = held?.intent;
    window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, {
      detail: {
        prompt: instruction,
        display: instruction,
        ...(intent ? { intent: { ...intent, instruction } } : {}),
      },
    }));
  };
  // AN ACT PRESSED WHILE A TURN IS RUNNING IS NEVER DROPPED (Vexa-ai/vexa#1594).
  //
  //  Founder walk 2026-09-06: *"extend this page button does not work when chat is working"*. The
  //  press did reach `onAsk`; `onAsk` did call `send`; and `send`'s first line returns on
  //  `state.busy`. The act evaporated between two functions — no bubble, no row, no error, nothing
  //  on the wire — while the control that produced it looked exactly as it does when it works.
  //
  //  So an ask that arrives mid-turn is never dropped. It used to QUEUE WHOLE in this component —
  //  display, prompt, intent and every option — and fire as its own turn when the current one
  //  ended; since Vexa-ai/vexa#1610 it is SUBMITTED TO THE SERVER at once instead, because a queue
  //  with one reader in one tab is a queue another device cannot see and a closed tab never fires.
  //  A page act (Extend / Create, the two kinds the server runs as background jobs —
  //  `chat_intents.JOB_KINDS`, which is what `isPageIntent` is) still puts a ROW at the foot of the
  //  transcript AT ONCE saying it is queued; the difference is that the row is then reconciled
  //  against the server's own pending list. Never a silent drop, and never a disabled control.
  useEffect(() => {
    const onAsk = (e: Event) => {
      const detail = (e as CustomEvent<{ mode?: "draft"; prompt?: string; display?: string; hidden?: boolean; ground?: boolean; session?: string; scaffoldId?: string; intent?: ChatIntent }>).detail;
      const prompt = detail?.prompt;
      if (!prompt) return;
      // A SESSION-TARGETED ask must never land in whichever chat happens to be visible (the
      // workspace-scaffold kickoff once fired into the org-setup thread mid-switch). Not ours →
      // stash it; the target session's Chat consumes it the moment it mounts.
      if (detail?.session && detail.session !== session) {
        if (detail.mode === "draft") return;
        try { localStorage.setItem(`vexa.pendingAsk.${detail.session}`, JSON.stringify({ prompt, display: detail.display, hidden: detail.hidden, ground: detail.ground, scaffoldId: detail.scaffoldId })); } catch { /* ignore */ }
        return;
      }
      if (layout.store.getState().rightCollapsed) layout.toggleRight();
      if (detail.mode === "draft") {
        setValue((current) => current ? `${current}\n\n${prompt}` : prompt);
        window.setTimeout(() => inputRef.current?.focus(), 0);
        return;
      }
      // `display` — what the READER sees when it is not what the agent gets: a chip whose label is
      // the user's own sentence renders as their message, and the grounding it carries does not.
      const display = detail?.display || prompt;
      const opts: SendOpts = { hidden: detail?.hidden, ground: detail?.ground, scaffoldId: detail?.scaffoldId, intent: detail?.intent };
      // MID-TURN: SUBMIT IT TO THE SERVER NOW (Vexa-ai/vexa#1610), and say so where there is
      // somewhere to say it. It used to be pushed onto `askQueue` — a list in this tab — and sent
      // when the turn ended, which is a queue with one reader in one browser: another device never
      // saw the press, and a closed tab never fired it.
      if (getChatState(chatKey).busy) {
        const rowId = newSubmissionId();
        // Only a PAGE act has a row to draw HERE, at the press: it names a file, which is what a job
        // row is about. Every other submission gets its row from the server's pending list a moment
        // later, and `inbox: true` is what lets that list replace this one instead of doubling it.
        const act = detail?.intent && isPageIntent(detail.intent) ? detail.intent : null;
        if (act) {
          updateChatState(chatKey, (s) => ({
            ...s, jobs: queueJob(s.jobs, { id: rowId, kind: act.kind, target: jobTarget(act), inbox: true }),
          }));
        }
        // …AND IN PLACE, ON THE CONTROL THAT WAS PRESSED (Vexa-ai/vexa#1604), in the row's own
        // words. EVERY job act, not only the page ones: a transcript passage names no file so it
        // draws no row in the chat, but it was pressed on a control standing under the reader's
        // cursor, and that control is owed the same answer.
        if (detail?.intent && isJobIntent(detail.intent)) {
          actQueued({ target: actTarget(detail.intent), kind: detail.intent.kind });
        }
        void submitToServer(rowId, withSay(prompt, detail?.display), withSay(prompt, detail?.display), opts);
        return;
      }
      void sendRef.current(display, withSay(prompt, detail?.display), withSay(prompt, detail?.display), opts);
    };
    window.addEventListener(ASK_CHAT_EVENT, onAsk);
    return () => window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  }, [layout, session, chatKey]);

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
        if (d.prompt) void sendRef.current(d.display || d.prompt, withSay(d.prompt, d.display), withSay(d.prompt, d.display), { hidden: d.hidden, ground: d.ground, scaffoldId: d.scaffoldId });
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

  // THE CHAT IS IDLE — CATCH UP WITH THE SERVER (Vexa-ai/vexa#1610).
  //
  //  Three things, in this order, and each is about the queue being the SERVER's:
  //   1. re-send anything a dropped POST left in the outbox (the one gap the server cannot see);
  //   2. re-read the pending list, so the queued rows are what the server holds and not what this
  //      browser remembers;
  //   3. if something is queued, ATTACH to watch it run — never re-send it. It is already on the
  //      inbox; posting it again is the double-turn this whole issue exists to prevent.
  //
  //  The attach is bounded by the queue HEAD: one view per head, and a head that has not moved is
  //  a view already open or one that ended with nothing taken. That is the stop condition — without
  //  it a wedged worker would be re-attached to forever.
  //
  //  IT RUNS ON MOUNT TOO, which is the reload / second window / swapped-container case: the chat
  //  comes up, reads the queue it never held, and watches it. One effect rather than a second one
  //  beside it, because two would both flush the outbox and could send one message twice.
  useEffect(() => {
    if (busy) return;
    // ONE AT A TIME, BUT NEVER A DROPPED ASK: a trigger that arrives while a catch-up is running
    // (the ack of a submission that was in flight) runs once more when that one finishes.
    if (catchingUp.current) { rerunCatchUp.current = true; return; }
    let cancelled = false;
    catchingUp.current = true;
    void (async () => {
      try {
        const sent = await flushOutbox(session, fetch, (sub, fault) =>
          updateChatState(chatKey, (s) => ({ ...s, jobs: blockSubmission(s.jobs,
            { id: sub.id, kind: sub.intent?.kind, display: sub.display ?? sub.prompt }, fault) })),
          (id) => inFlightRef.current.has(id));
        if (sent.length) {
          updateChatState(chatKey, (s) => ({ ...s, jobs: s.jobs.filter((j) => !(j.outbox && sent.includes(j.id))) }));
        }
        // A VIEW STILL OPEN (a job's) is still reading the Stream, so "started after my cursor" is
        // not a question this moment can ask — the cursor is that view's, and it is not done.
        const watching = openViewsRef.current > 0;
        const view = await refreshPending(watching ? "" : cursorRef.current);
        // NOTHING BLOCKED IS WATCHED (P18). A row the runtime refused has no worker coming for it,
        // so attaching would wait out the whole timeout and then say the agent did not respond —
        // the second half of the lie. Only a row that can run is worth a view.
        //
        // …AND NOTHING TAKEN IS LEFT UNWATCHED (the founder, 2026-10-10: *"sometimes chat does not
        // answer if asked while it's not yet answered"*). A message submitted mid-turn is taken by
        // the worker the instant the turn in front ends; a chat that only watched PENDING rows
        // found nothing here and the answer streamed to nobody. `taken` says a turn started after
        // the last event this chat read, and that is worth a view exactly as a queued row is.
        const want = turnsToWatch(view, watching ? "" : cursorRef.current);
        if (cancelled || !want || getChatState(chatKey).busy) return;
        const head = want.head;
        if (attachedRef.current === head) return;
        attachedRef.current = head;
        // The cursor of the stream that just ended is EXACT — the next events after it are the
        // queued turn's. With none (a reload, a second window) the server's own tail is the honest
        // second best: the turn may already be under way, and its beginning is in the transcript.
        void send("", "", "", { attachFrom: attachPoint(view, cursorRef.current) });
      } finally {
        catchingUp.current = false;
        if (rerunCatchUp.current) { rerunCatchUp.current = false; setCatchUpTick((n) => n + 1); }
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, session, catchUpTick]);

  const onSubmit = async (opts: { answered?: boolean; plain?: boolean } = {}) => {
    const v = withPastes(value.trim(), pastes);
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
      if (!v || hasAttachments) return;   // submit plain text only; attachments wait for idle
      // ON THE SERVER, NOW (Vexa-ai/vexa#1610). This used to push onto `queuedRef` — a list in this
      // tab — and send when the turn ended. The founder sent two messages into a busy session on
      // 2026-09-02 and neither became a turn; that failure was a lost POST, and this one is its
      // sibling: a message nobody but this browser had. The bubble still appears immediately, the
      // words go to the session's inbox immediately, and the queued row that follows is the
      // SERVER's answer about what is waiting.
      const qid = newSubmissionId();
      updateChatState(chatKey, (s) => ({ ...s, turns: [...s.turns, { id: qid, role: "user", text: v, at: Date.now() / 1000 }] }));
      stick.pin();   // queued or not, the person just wrote — show them their own turn
      clearInput();
      void submitToServer(qid, isRoutineCommand(v) ? routineCreationPrompt(v) : v, v, {});
      return;
    }
    stick.pin();   // sending re-attaches follow-the-stream — the human asked for this reply
    if (!hasAttachments && isRoutineCommand(v)) { void send(v, routineCreationPrompt(v)); clearInput(); return; }
    if (!hasAttachments && v.startsWith("/")) { const sk = commands.querySkills(v)[0]; if (sk) { void commands.execute(sk.id, v); clearInput(); return; } }
    // @-MENTIONS (ADR-0048): a mentioned person who is not in this chat yet is a question, asked once
    // before the message goes — share this chat with them? Nothing is sent until it is answered.
    const live = hasAttachments ? [] : mentionsInText(v, picked);
    // only a person picked to be INVITED (to the chat or a call) is a share question; a tag is not
    const inviting = live.filter((m) => m.intent === "chat" || m.intent === "call");
    if (inviting.length && !opts.answered) {
      let check: ShareCheck = { kind: "send" };
      try { check = await checkShare(session, inviting); } catch { check = { kind: "send" }; }
      if (check.kind === "confirm") { setConfirm({ check, text: v, busy: false }); return; }
    }
    const mentionIds = opts.plain ? undefined : (live.length ? live.map((m) => m.subject) : undefined);
    let prompt = isRoutineCommand(v) ? routineCreationPrompt(v) : v;
    const displayText = v;
    const referenceSource = v;
    let sentFiles: MessageAttachment[] = [];
    if (hasAttachments) {
      setUploading(true);
      setUploadError(null);
      const settled = await awaitAttachments(attachments);
      setUploading(false);
      if (typeof settled === "string") { setUploadError(settled); return; }
      const uploaded = settled;
      prompt = attachmentPrompt(prompt, uploaded);
      sentFiles = sentAttachments(session, uploaded);
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
    void send(displayText, prompt, referenceSource, { mentions: mentionIds, ...(sentFiles.length ? { attachments: sentFiles } : {}) });
    if (thisRoom?.room && live.length && !opts.plain) {
      void checkRoomTags(session, live).then((cards) => { if (cards.length) setTagCards((c) => [...c, ...cards]); });
    }
    // INVITED TO A CALL: the call starts (or is joined) here, and everyone in the chat is rung
    if (live.some((m) => m.intent === "call")) window.dispatchEvent(new CustomEvent(CALL_START_EVENT, { detail: { session } }));
    clearInput();
    setPicked([]);
    setChoice(null);
  };

  /** "Share and send" (`all`: they join this chat, history included) or "New chat with them"
   *  (`none`): add the mentioned people, then send. A new shared chat: open it, and send there. */
  const shareAndSend = async (history: "all" | "none" | null) => {
    if (!confirm || confirm.check.kind !== "confirm") return;
    const { check, text } = confirm;
    setConfirm({ ...confirm, busy: true, error: undefined });
    try {
      const target = await shareWith(session, check.people, history);
      const ids = mentionsInText(text, picked).map((m) => m.subject);
      setConfirm(null);
      if (target !== session) {
        pendingFirstSend.set(target, { text, mentions: ids });
        clearInput(); setPicked([]);
        window.dispatchEvent(new CustomEvent(OPEN_SESSION_EVENT, { detail: { session: target } }));
        if (!minutesOnly()) layout.setActiveSession(target);
        return;
      }
      await onSubmit({ answered: true });
    } catch (e) {
      setConfirm({ ...confirm, busy: false, error: presentError(e).headline || "Couldn't share this chat. Try again." });
    }
  };

  // …and the message waiting for that chat, sent once it is the open one.
  useEffect(() => {
    const p = pendingFirstSend.get(session);
    if (!p || busy) return;
    pendingFirstSend.delete(session);
    stick.pin();
    void send(p.text, p.text, p.text, { mentions: p.mentions, toAgent: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);

  // THE @ PICKER — the people the server says this person may mention, fetched once, on the first @.
  const mq = activeMention(value, caret);
  useEffect(() => {
    if (!mq || mentionPeople || !mentionOn) return;
    let live = true;
    // THE SAME PEOPLE AS THE HOME (founder: "all those people should be available as @"): everyone
    // the caller has interacted with, most recent first; the agents from the mention list
    Promise.all([listKnownPeople().catch(() => ({ people: [], count: 0 })), listMentionable().catch(() => ({ people: [], agents: [{ name: "vexa" }] }))])
      .then(([known, m]) => { if (live) setMentionPeople({ people: known.people.length ? knownAsMentionable(known.people) : m.people, agents: m.agents }); });
    return () => { live = false; };
  }, [mq !== null, mentionPeople, mentionOn]);   // eslint-disable-line react-hooks/exhaustive-deps
  const pickRows: PickerRow[] = mq && mentionPeople && !pickClosed && mentionOn ? pickerRows(mentionPeople.people, mentionPeople.agents, mq.query) : [];
  // WHAT THE PICK IS FOR (founder: "to tag and to invite to a call"): a small choice after the
  // chip, a tag by default
  const [choice, setChoice] = useState<{ person: MentionablePerson; label: string } | null>(null);
  const pick = (row: PickerRow) => {
    if (!mq) return;
    // a Vexa account is an @-mention (it can be invited); anyone else is a reference only — their
    // page's chip when they have one, else their name — and never sends anything
    const ref = row.kind === "person" && !(row.person.vexa ?? !!row.person.subject);
    const next = ref ? insertReference(value, mq, row.kind === "person" ? row.person : null) : insertMention(value, mq, row.label);
    setValue(next.text);
    setCaret(next.caret);
    if (row.kind === "person") {
      if (!ref) {
        const m: Mention = { subject: row.person.subject, label: row.label, name: row.person.name || row.label, intent: "tag" };
        setPicked((prev) => [...prev.filter((x) => x.subject !== m.subject), m]);
      }
      setChoice({ person: row.person, label: row.label });
    }
    setPickIdx(0);
    window.setTimeout(() => { const el = inputRef.current; if (el) { el.focus(); el.setSelectionRange(next.caret, next.caret); } }, 0);
  };
  const shownMentions = mentionsInText(value, picked);

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    // a screenshot, or files copied in the file manager — attached, not pasted as text
    const files = pastedFiles(e.clipboardData);
    if (files.length === 0) {
      const text = e.clipboardData?.getData("text/plain") ?? "";
      if (isLongPaste(text)) { e.preventDefault(); setPastes((p) => [...p, text]); return; }
      // A PASTED LINK TO ANYTHING OURS becomes its live chip (#1783, 7gg): `…/r/chat/<id>` → `[[chat:<id>]]`,
      // and a copied reference (its HTML flavour) too. Two edits, so ⌘Z brings the link back (S7).
      const chips = e.clipboardData ? referenceFromPaste(e.clipboardData) : null;
      if (chips !== null) {
        e.preventDefault();
        setValue(pasteToChip(e.currentTarget, text, chips));
        return;
      }
      setMeetingOffer(meetingLinkIn(text));
      return;
    }
    e.preventDefault();
    addFiles(files);
  };

  const slash = value.startsWith("/");
  const skills = slash ? commands.querySkills(value) : [];

  // THE INPUT FIELD IS FOR TYPING. F66's second half — "working · 18 steps · entity_upsert" beside
  // the stop button — is GONE (founder ruling 2026-09-06, Vexa-ai/vexa#1587, on a screenshot of the
  // composer reading `working · 1 step · james-hollister.md` while the chat above already said
  // `Reading · james-hollister.md · 1 step` and `Working…`): *"working · 2 steps · whats_waiting —
  // remove that from the input field"*. A running turn — and a running job — is told ONCE, in the
  // chat, where the step rows are (`JobRows` below, and the turn's own op line in agent-window).
  // The stop control stays: that is a handle, not narration.

  // THE COMPOSER, IN PLACE: a surface in the empty chat (the home) may draw it under its greeting.
  const [composerInline, setComposerInline] = useState(false);
  const composer = (
    <>
      {/* THE STANDING ROW that used to sit here — "Open transcript" · "Open note" (#1586) — is
          gone with the chips it carried (Vexa-ai/vexa#1600, founder: *"just keep a tab that can't
          be closed instead"*). It was this component's only host slot above the composer, so the
          slot goes too rather than standing empty waiting for a second reason to exist. */}
      <MentionPicker rows={pickRows} active={Math.min(pickIdx, Math.max(0, pickRows.length - 1))} onPick={pick} />
      {choice && !pickRows.length && (
        <MentionChoice name={choice.person.name} vexa={!!(choice.person.vexa ?? choice.person.subject)}
          onChoose={(intent) => {
            const who = choice.person;
            setChoice(null);
            if (intent === "tag") return;
            if (who.vexa ?? !!who.subject) {
              setPicked((prev) => prev.map((m) => (m.subject === who.subject ? { ...m, intent } : m)));
              return;
            }
            // someone not on Vexa: the call starts and the agent DRAFTS the invitation with its link;
            // nothing goes out until the person confirms the final text
            const d = callInviteDraft(who);
            window.dispatchEvent(new CustomEvent(CALL_START_EVENT, { detail: { session } }));
            window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { session, prompt: d.prompt, display: d.say } }));
          }} />
      )}
      {tagCards.length > 0 && (
        <TagCards session={session} answers={tagCards} onDismiss={(i) => setTagCards((c) => c.filter((_x, j) => j !== i))} />
      )}
      {confirm && confirm.check.kind === "confirm" && (
        <ShareConfirm people={confirm.check.people} owner={confirm.check.owner} preview={confirm.check.preview}
          busy={confirm.busy} error={confirm.error}
          onShare={(h) => void shareAndSend(h)}
          onSendPlain={() => { setConfirm(null); void onSubmit({ answered: true, plain: true }); }}
          onCancel={() => { setConfirm(null); inputRef.current?.focus(); }} />
      )}
      {slash && skills.length > 0 && (
        <div style={{ border: "1px solid var(--line2)", borderRadius: 11, background: "var(--panel)", overflow: "hidden" }}>
          {skills.map((c) => <div key={c.id} onMouseDown={() => setValue(c.skill! + " ")} style={{ display: "flex", gap: 10, padding: "9px 12px", cursor: "pointer", fontSize: 13 }}><code style={{ fontFamily: "var(--mono)", color: "var(--accent)", minWidth: 88 }}>{c.skill}</code><span style={{ color: "var(--t3)", fontSize: 12 }}>{c.title}</span></div>)}
        </div>
      )}
      <div
        style={{ border: "1px solid var(--line2)", borderRadius: 14, background: "var(--panel)", padding: "10px 10px 6px 12px", display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}
      >
        {((advertiseFocus && contextRef) || (!minutesOnly() && (ambientEligible || includeSchedule === true)) || (bundleFocus && (bundleFocus.kind === "workspace" || bundleFocus.kind === "today"))) && (
          <div data-composer-context>
          {/* ONE LINE OF CONTEXT CHIPS (guidelines §3.3): a "+N" chip says how many are out of
              view and opens the row; nothing is silently clipped. Chips are interactive (each
              removes or re-adds a piece of context), so they wear the Chip shape — no dashed
              borders, no uppercase "FOCUS" label: the crosshair icon says it. */}
          <ChipRow label="context chips">
            {/* ambient schedule chip — the context bundle's always-visible half: on = the agent
                sees today's schedule; × turns it off; ghost chip re-adds. HIDDEN in minutes mode
                for now (founder 2026-08-22) — the context still flows, the chip just doesn't. */}
            {minutesOnly() ? null : ambientOn ? (
              <Chip icon={<Icon name="cal" size={14} />} title="The agent sees your schedule (today, upcoming, live) on this surface"
                onRemove={() => setAmbient(false)} removeLabel="Remove schedule context">Schedule · today</Chip>
            ) : ambientEligible ? (
              <Chip ghost icon={<Plus size={14} strokeWidth={1.75} />} title="Include your schedule in the agent's context" onClick={() => setAmbient(null)}>Schedule</Chip>
            ) : null}
            {/* B4 carve: the meeting focus is ONE chip — `Preparing · Title ×` — never a mono
                uppercase label plus a second raw-id Focus chip for the same meeting. */}
            {advertiseFocus && contextRef && contextRef.kind === "meeting" && activeMeeting ? (() => {
              const mode = MODE_CHIP[meetingPhase(activeMeeting)];
              return (
                <Chip icon={<span className="vx-dot" data-tone={mode.tone} />} title={`This chat is grounded in the meeting's ${mode.label.toLowerCase()} state`}
                  onRemove={() => setFocusCleared(true)} removeLabel="Clear focus">{mode.label} · {meetingLabel(activeMeeting)}</Chip>
              );
            })() : advertiseFocus && contextRef ? (
              <Chip icon={<Crosshair size={14} strokeWidth={1.75} aria-label="Focus" />} title={`Focus: ${contextRef.raw}`}
                onRemove={() => setFocusCleared(true)} removeLabel="Clear focus">{contextRef.kind === "file" ? fileLabel(contextRef.value) : contextRef.value}</Chip>
            ) : null}
            {(!advertiseFocus || !contextRef) && bundleFocus && (bundleFocus.kind === "workspace" || bundleFocus.kind === "today") && (
              <Chip icon={bundleFocus.kind === "workspace" ? <Crosshair size={14} strokeWidth={1.75} aria-label="Focus" /> : <Icon name="cal" size={14} />}
                onRemove={() => setFocusCleared(true)} removeLabel="Clear focus">
                {bundleFocus.kind === "workspace" ? `Workspace · ${bundleFocus.slug}` : "Today"}
              </Chip>
            )}
          </ChipRow>
          </div>
        )}
        <ComposerReferences text={value} />
        <MentionChips mentions={shownMentions} onRemove={(m) => setPicked((prev) => prev.filter((x) => x.subject !== m.subject))} />
        {meetingOffer && <div className="vx-paste-tray" data-send-vexa="">
          <KitButton size="sm" onClick={() => { const m = meetingOffer; setMeetingOffer(null); void sendVexa(m); }}>Send Vexa to this meeting</KitButton>
          <KitButton size="sm" variant="ghost" aria-label="Dismiss" onClick={() => setMeetingOffer(null)}>×</KitButton></div>}
        {pastes.length > 0 && <div className="vx-paste-tray">{pastes.map((t, i) => (
          <PasteChip key={i} text={t} onRemove={() => setPastes((p) => p.filter((_, j) => j !== i))}
            onEdit={() => { setPastes((p) => p.filter((_, j) => j !== i)); setValue((v) => (v ? `${v}\n\n${t}` : t)); inputRef.current?.focus(); }} />
        ))}</div>}
        <AttachmentTray attachments={attachments} onRemove={removeAttachment} onRetry={retryAttachment}
          onPreview={(a) => setPreview(a)} onExit={() => inputRef.current?.focus()} />
        {uploadError && <div style={{ color: "var(--danger)", fontSize: 12, lineHeight: 1.35 }}>{uploadError}</div>}
        {micError && <div style={{ color: "var(--danger)", fontSize: 12, lineHeight: 1.35 }}>{micError}</div>}
        {mic !== "idle" && (
          <div data-dictation-status role="status" aria-live="polite" className="vx-dictation">
            <progress className="vx-dictation-meter" max={100} value={Math.round(micLevel * 100)} aria-label="Microphone level" />
            <span>{mic === "stt" ? "Finishing…" : `Listening ${Math.floor(micElapsed / 60)}:${String(micElapsed % 60).padStart(2, "0")}`}</span>
            {mic === "rec" && <span className="vx-dictation-hint">Esc or the mic to stop · Enter sends</span>}
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ATTACHMENT_ACCEPT}
          onChange={(e) => { addFiles(Array.from(e.target.files ?? [])); e.currentTarget.value = ""; }}
          style={{ display: "none" }}
        />
        {/* THE CLAUDE CODE COMPOSER (founder 2026-10-10): the text on top at full width, growing
            with what is typed; under it one toolbar — attach and dictate as quiet icons on the
            left, the chat's model and effort as plain text and send on the right. THE TOOLBAR NEVER
            WRAPS (guidelines §3.3): it answers to the composer's own width — below 560px the
            effort shows as an icon, below 400px attach and dictate move into a "⋯" menu — and the
            model and send controls are always visible. */}
        <textarea
          data-ring-host
          ref={inputRef}
          value={value}
          onChange={(e) => { setValue(e.target.value); micDictRef.current?.edited(e.target.value); setCaret(e.target.selectionStart ?? e.target.value.length); setPickClosed(false); setPickIdx(0); if (confirm) setConfirm(null); }}
          onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
          onInput={(e) => resizeComposerTextarea(e.currentTarget)}
          onPaste={onPaste}
          onKeyDown={(e) => {
            if (pickRows.length) {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setPickIdx((i) => (i + (e.key === "ArrowDown" ? 1 : pickRows.length - 1)) % pickRows.length);
                return;
              }
              if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(pickRows[Math.min(pickIdx, pickRows.length - 1)]); return; }
              if (e.key === "Escape") { e.preventDefault(); setPickClosed(true); return; }
            }
            if (e.key === "Escape" && confirm) { e.preventDefault(); setConfirm(null); return; }
            if (e.key !== "Enter" || e.shiftKey) return;
            e.preventDefault();
            if (confirm) return;          // the question above is the answer to this Enter
            void onSubmit();
          }}
          placeholder={composerPlaceholder(room) ?? (contextRef?.kind === "meeting" && activeMeeting
            ? MODE_PLACEHOLDER[meetingPhase(activeMeeting)]
            : "Type / for skills, or ask the agent…")}
          disabled={uploading}
          rows={2}
          style={{ width: "100%", boxSizing: "border-box", background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 14, lineHeight: "20px", minWidth: 0, minHeight: 44, maxHeight: MAX_TEXTAREA_HEIGHT, resize: "none", overflowY: "hidden", padding: "2px 2px", margin: 0, fontFamily: "inherit" }}
        />
        <div data-composer-toolbar className="vx-container" style={{ display: "flex", alignItems: "center", flexWrap: "nowrap", gap: 4, minWidth: 0 }}>
          <Fold at={400} wide={<>
            <IconButton label="Attach files" disabled={busy || uploading} onClick={() => fileInputRef.current?.click()}>
              <Paperclip size={16} strokeWidth={1.75} />
            </IconButton>
            <IconButton label={mic === "rec" ? "Stop recording" : mic === "stt" ? "Transcribing…" : "Dictate"}
              pressed={mic === "rec"} loading={mic === "stt"} disabled={uploading || mic === "stt"} onClick={() => void toggleMic()}>
              <Mic size={16} strokeWidth={1.75} />
            </IconButton>
          </>} narrow={
            <Menu label="More composer actions" placement="top" triggerData={{ "data-composer-more": "" }}
              trigger={<Ellipsis size={16} strokeWidth={1.75} aria-hidden />}
              items={[
                { key: "attach", label: "Attach files", icon: <Paperclip size={14} strokeWidth={1.75} />, disabled: busy || uploading, onSelect: () => fileInputRef.current?.click() },
                { key: "mic", label: mic === "rec" ? "Stop recording" : mic === "stt" ? "Transcribing…" : "Dictate", icon: <Mic size={14} strokeWidth={1.75} />, disabled: uploading || mic === "stt", onSelect: () => void toggleMic() },
                // below 400px the effort control leaves the toolbar and its levels live here
                ...(effortItems ?? []),
              ]} />
          } />
          <div style={{ flex: "1 1 auto", minWidth: 0, display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 4 }}>
            {/* THE MODEL THIS CHAT RUNS ON, AND ITS EFFORT (ADR-0043) — beside send, because the next
                turn is when a pick takes effect. Absent on a deployment with no catalog. */}
            {levelsOn
              ? <ModelChip session={session} hasMessages={turns.length > 0} />
              : <ModelPicker session={session} onEffortItems={setEffortItems} />}
            {busy
              ? <IconButton label="Stop" variant="secondary" onClick={stop}><Square size={12} strokeWidth={2.5} fill="currentColor" /></IconButton>
              : <IconButton label="Send" variant="primary" disabled={uploading} onClick={() => void onSubmit()}><Send size={16} strokeWidth={1.75} /></IconButton>}
          </div>
        </div>
      </div>
    </>
  );

  const composerSlot = { composer, claim: setComposerInline };
  return (
    <FileDropZone onFiles={addFiles} onFolders={folderDropOn ? (entries) => requestFolderImport({ kind: "entries", entries, into: null }) : undefined}>
    {preview?.previewUrl && <ImagePreview attachment={preview} onClose={() => setPreview(null)} />}
    <ComposerSlotProvider value={composerSlot}>
    <AskSessionProvider value={session}>{/* 7g: an ```ask card in this chat opens its pending item here (S5) */}
    <AgentWindow top={<ChatHeader subject={subject} session={session} onSelectSession={selectSession} onNewChat={newChat} onClose={() => layout.toggleRight()} />} scrollRef={scrollRef} composer={composerInline ? null : composer}>
      {/* THE EMPTY STATE. The centered "What organisation are you? / Just the name is enough — I'll
          research the rest…" card that used to sit here is DELETED (F37): it was the pre-scaffold
          admin onboarding, reachable only from an `org-setup` session the rail's own seeding
          planted, and it promised the reader a research step that does not exist. The founder met
          it in a chat he never made — *"I explain this as stale code."*
          What is left renders the meeting greeting when there IS a meeting, and otherwise renders
          nothing but whatever the host put in `emptyExtra`. */}
      {/* THE READING COLUMN (founder 2026-10-15: "comfy readability"): a conversation reads in a
          centred ~70-character column; the empty home keeps its own width */}
      <div className={turns.length || room?.lines.length ? "vx-read-col" : undefined}>
      <MessageScope.Provider value={session}><ChatConversation turns={turns} busy={busy || loading} jobs={chatState.jobs} room={room} onContinue={continueTurn} onStopRunning={(t) => stopTurn(t.failed?.fault.session || session)} onDecide={decideTarget} onRetryRow={(j) => { void retryRow(j); }} onSendNow={async (j) => { const ok = await sendNow(session, j.id); void refreshPending(); return ok; }}
        onEditQueued={async (j) => {
          if (!(await withdrawQueued(session, j.id))) { void refreshPending(); return; }
          updateChatState(chatKey, (s) => ({ ...s, turns: s.turns.filter((t) => t.id !== j.id) }));
          setValue((v) => (v ? `${v}\n\n${j.text ?? ""}` : (j.text ?? "")));
          inputRef.current?.focus();
          void refreshPending();
        }}
        onRemoveQueued={async (j) => {
          if (await withdrawQueued(session, j.id)) updateChatState(chatKey, (s) => ({ ...s, turns: s.turns.filter((t) => t.id !== j.id) }));
          void refreshPending();
        }} empty={!loading && minutesOnly() && minutesEmptyGreeting(session)
          // A MEETING'S GREETING is the agent speaking: its line where a turn goes, not a centred box
          ? <div data-meeting-greeting=""><MeetingGreeting session={session} />{emptyExtra}</div>
          :
        <div style={{ color: minutesOnly() ? "var(--t2)" : "var(--t3)", fontSize: 13, textAlign: minutesOnly() ? "left" : "center", lineHeight: 1.6, maxWidth: composerInline ? "none" : 560, margin: minutesOnly() ? (composerInline ? "8px auto 0" : "26px auto 0") : "40px 0 0", padding: minutesOnly() ? "0 22px" : 0 }}>
            {loading ? "Loading conversation…" : (minutesOnly()
              ? <MeetingGreeting session={session} />
              : "Ask the agent to record, research, or restructure knowledge — it writes to your git workspace and commits.")}
            {/* NOT while the history is still loading: a chip that appears and then vanishes under
                an arriving conversation is worse than one that arrives a beat late. */}
            {!loading && emptyExtra}
          </div>} /></MessageScope.Provider>
      </div>
      <BundleImportCards cards={bundleImports.cards} onDismiss={bundleImports.dismiss} />
      <FolderImportedCards cards={folderCards.cards} />
      {/* JUMP TO LATEST — the only way back to following that costs the reader nothing. Sticky to
          the bottom of the scroller and zero-height, so it adds no scroll height of its own and the
          window shell (owned by parallel work) is untouched. */}
      {!following && !composerInline && (
        <div style={{ position: "sticky", bottom: 0, height: 0, display: "flex", justifyContent: "center", pointerEvents: "none" }}>
          <button type="button" onClick={() => stick.pin()} aria-label="Jump to latest" title="Jump to latest"
            style={{ pointerEvents: "auto", transform: "translateY(-10px)", display: "inline-flex", alignItems: "center", gap: 6, background: "var(--panel2)", color: "var(--t1)", border: "1px solid var(--line2)", borderRadius: 999, padding: "5px 12px", fontSize: 12, cursor: "pointer", boxShadow: "0 2px 10px rgba(0,0,0,.18)" }}>
            <Icon name="chevR" size={13} style={{ transform: "rotate(90deg)" }} />Jump to latest
          </button>
        </div>
      )}
    </AgentWindow>
    </AskSessionProvider>
    </ComposerSlotProvider>
    </FileDropZone>
  );
}

// Agent /-skills — absent in meetings-only mode (NEXT_PUBLIC_TERMINAL_MODE=meetings), where the chat
// rail itself doesn't render (Workbench) and the proxy refuses agent paths.
if (!meetingsOnly()) {
  registerCommand({ id: "skill.research", title: "Research and file to the workspace", skill: "/research", run: () => {} });
  registerCommand({ id: "skill.draft", title: "Draft an email or doc", skill: "/draft", run: () => {} });
  registerCommand({ id: "skill.routine", title: "Create a scheduled routine", skill: ROUTINE_COMMAND, run: () => {} });
}


/** Fired when this tab posted a line to the chat, so the lines are read at once. */
export const ROOM_LINES_EVENT = "vexa:terminal:room-lines";

/** THE LINES IN A SHARED CHAT (#1783, collaboration rehearsal 2026-10-14): a message one person
 *  posts without addressing the agent was stored and shown to nobody. They are the chat's own
 *  (`GET /api/sessions/{s}/messages`), every participant reads them, and they show here with who
 *  wrote each, newest last — read every few seconds while the chat is open (and at once after this
 *  tab posts one), so they arrive live and are still there after a reload. The same read carries
 *  the conversation's `version`: when another person's turn lands it moves, and `onTurn` re-reads
 *  the conversation. A chat the store does not hold answers 404 and shows nothing. */
export type RoomLine = { seq: number; author: string; text: string; created: number };
export type RoomStep = { tool: string; file_path?: string };
export type RoomTurn = "thinking" | "nothing" | null;
export type RoomView = { lines: RoomLine[]; you: string; names: Record<string, string>; turn?: RoomTurn; step?: RoomStep | null };

/** THE ROOM'S LIVE STATUS, above the composer (founder, app.dev 2026-10-16): "Vexa heard you ·
 *  thinking…" from the moment a wake is accepted, the step the turn is on under it, replaced by the
 *  reply; "Vexa: nothing to add" while `showingNothing` (3 s) after a turn that said nothing. */
export function roomTurnRow(turn: RoomTurn | undefined, step: RoomStep | null | undefined, showingNothing: boolean): { line: string; step: string | null } | null {
  if (turn === "thinking") {
    const label = step?.tool ? toolOp(step.tool, step.file_path ? { file_path: step.file_path } : undefined).label : "";
    const file = step?.file_path ? step.file_path.split("/").pop() : "";
    return { line: "Vexa heard you \u00b7 thinking\u2026", step: label ? (file && !label.includes(file) ? `${label} \u00b7 ${file}` : label) : null };
  }
  if (turn === "nothing" && showingNothing) return { line: "Vexa: nothing to add", step: null };
  return null;
}

function RoomTurnStatus({ turn, step }: { turn?: RoomTurn; step?: RoomStep | null }) {
  const [showingNothing, setShowingNothing] = useState(false);
  useEffect(() => {
    if (turn !== "nothing") { setShowingNothing(false); return; }
    setShowingNothing(true);
    const t = window.setTimeout(() => setShowingNothing(false), 3000);
    return () => window.clearTimeout(t);
  }, [turn]);
  const row = roomTurnRow(turn, step, showingNothing);
  if (!row) return null;
  return (
    <div data-room-turn={turn || ""} role="status" aria-live="polite" className="vx-room-turn">
      <span className="vx-room-turn-line">
        {turn === "thinking" && <span className="vx-op-spin vx-room-turn-spin" />}
        {row.line}
      </span>
      {row.step && <span data-room-step="" className="vx-room-turn-step">{row.step}</span>}
    </div>
  );
}

export function useRoomLines(session: string, onTurn?: () => void): RoomView {
  const [lines, setLines] = useState<RoomLine[]>([]);
  const [you, setYou] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const [turn, setTurn] = useState<RoomTurn>(null);
  const [step, setStep] = useState<RoomStep | null>(null);
  const turnRef = useRef(onTurn);
  turnRef.current = onTurn;
  useEffect(() => {
    let live = true;
    let stop = false;
    let seen = -1;
    // A chat the store does not hold YET answers 404 — a new private chat, until its first turn
    // lands or it is shared. It is read again, slower, never given up on: giving up left the owner
    // of a chat shared after its first turn blind to every line the other person wrote (founder's
    // call 2026-10-14). Only "not configured" (503) stops.
    let quiet = 0;
    const read = () => {
      if (stop) return;
      sessionRoomLines(session).then((m) => {
        if (!live) return;
        quiet = 0;
        setLines(m.messages); setYou(m.you); setNames(m.names);
        setTurn(m.agent_turn ?? null); setStep(m.agent_step ?? null);
        // SHOW TO ROOM (JTBD-1): what a member shows the room opens in this member's panel, once
        window.dispatchEvent(new CustomEvent(ROOM_SHOWING_EVENT, { detail: { session, showing: m.showing, you: m.you, names: m.names } }));
        if (seen >= 0 && m.version > seen) turnRef.current?.();
        seen = m.version;
      }).catch((e: unknown) => {
        const st = (e as { status?: number })?.status;
        if (st === 503) stop = true; else quiet += 1;
      });
    };
    setLines([]); setNames({}); setTurn(null); setStep(null);
    read();
    const now = () => { quiet = 0; read(); };
    window.addEventListener(ROOM_LINES_EVENT, now);
    let ticks = 0;
    const t = window.setInterval(() => {
      ticks += 1;
      if (typeof document !== "undefined" && document.hidden) return;
      if (quiet > 0 && ticks % 5 !== 0) return;   // unknown to the store: every 15 s
      read();
    }, 3000);
    return () => { live = false; window.clearInterval(t); window.removeEventListener(ROOM_LINES_EVENT, now); };
  }, [session]);
  return { lines, you, names, turn, step };
}

export const roomWho = (v: RoomView, a: string) => a === "vexa" ? "Vexa" : a === v.you ? "You" : (v.names[a] || "Someone");

/** WHERE EACH LINE GOES: before the first turn said after it. A turn the history gave no time takes
 *  the one before it (a live reply follows its question); leading undated turns take the next one.
 *  A conversation with no times at all keeps its lines after it. */
export function interleave(turns: Turn[], lines: RoomLine[]): ({ kind: "turn"; turn: Turn; i: number } | { kind: "line"; line: RoomLine })[] {
  const sorted = [...lines].sort((a, b) => a.created - b.created || a.seq - b.seq);
  const known = turns.map((t) => ("at" in t && typeof t.at === "number" ? t.at : null));
  if (known.every((x) => x === null)) {
    return [...turns.map((turn, i) => ({ kind: "turn" as const, turn, i })), ...sorted.map((line) => ({ kind: "line" as const, line }))];
  }
  const eff: number[] = [];
  for (let i = 0; i < turns.length; i++) eff[i] = known[i] ?? (i > 0 ? eff[i - 1] : NaN);
  for (let i = turns.length - 1; i >= 0; i--) if (Number.isNaN(eff[i])) eff[i] = eff[i + 1];
  const out: ({ kind: "turn"; turn: Turn; i: number } | { kind: "line"; line: RoomLine })[] = [];
  let j = 0;
  turns.forEach((turn, i) => {
    while (j < sorted.length && sorted[j].created < eff[i]) out.push({ kind: "line", line: sorted[j++] });
    out.push({ kind: "turn", turn, i });
  });
  while (j < sorted.length) out.push({ kind: "line", line: sorted[j++] });
  return out;
}

/** AN ACT'S INSTRUCTION IS NEVER DRAWN AS SOMEBODY'S WORDS (live app.dev: "Update notes" showed its
 *  whole internal instruction as the person's line). The server keeps such text out of the record;
 *  this is the last guard: a turn or a line that carries the machinery's own phrases is drawn as the
 *  act's short line, and the instruction stays in the record, auditable, unrendered. */
const MACHINERY_PHRASES = /<!--\s*(?:meeting:|vexa:transcript)|transcript_cursor|next_index|\[vexa-(?:job|act|flow|msg):/;
/** A home act's hidden tail (`homePrompt`): the agent's instruction, never the person's words. */
const HOME_TAIL = /\s*\(This came from your home, item [\s\S]*$/;
/** THE PERSON'S LINE, KEPT WITH A HIDDEN INSTRUCTION (founder, live: the "Create a shared workspace"
 *  starter posted its whole internal prompt — tool names and all — as his message). A starter, a
 *  chip or a home button sends the agent its instruction and the person's short line together; the
 *  record carries both, the line after this mark, and only the line is ever drawn. agent-api's
 *  `chat_label` names the chat from the same line. */
export const SAY_MARK = "\n\n<!-- vexa:say -->";
export function withSay(prompt: string, display?: string | null): string {
  const say = String(display ?? "").trim();
  return say && say !== prompt.trim() && !prompt.includes(SAY_MARK) ? `${prompt}${SAY_MARK}${say}` : prompt;
}

export function shownWords(text: string): string {
  const said = text.indexOf(SAY_MARK.trim());
  if (said >= 0) return text.slice(said + SAY_MARK.trim().length).trim() || "Ran an action";
  if (HOME_TAIL.test(text)) text = text.replace(HOME_TAIL, "").trim() || "Ran an action";
  if (!MACHINERY_PHRASES.test(text)) return text;
  if (/transcript/i.test(text) && /meeting/i.test(text)) return "Update notes from the transcript";
  const head = text.split("\n")[0].replace(MACHINERY_PHRASES, "").trim();
  return /^(Extend|Create|Explore|Highlight)\b/.test(head) ? head.slice(0, 120) : "Ran an action";
}

type Streamed = { kind: "turn"; turn: Turn; i: number } | { kind: "line"; line: RoomLine };
/** A ROUTINE'S REPEATED NOTICE IS ONE LINE (live app.dev, a routine chat with the same "couldn't tell
 *  how this run ended" five times in a row): consecutive identical lines by one author fold into one
 *  entry that carries them all. */
export function groupRepeats(items: Streamed[]): (Streamed | { kind: "repeat"; lines: RoomLine[] })[] {
  const out: (Streamed | { kind: "repeat"; lines: RoomLine[] })[] = [];
  for (const x of items) {
    const prev = out[out.length - 1];
    if (x.kind === "line" && prev) {
      const head = prev.kind === "line" ? prev.line : prev.kind === "repeat" ? prev.lines[0] : null;
      if (head && head.author === x.line.author && head.text.trim() === x.line.text.trim()) {
        out[out.length - 1] = { kind: "repeat", lines: prev.kind === "repeat" ? [...prev.lines, x.line] : [head, x.line] };
        continue;
      }
    }
    out.push(x);
  }
  return out;
}
const hhmm = (sec: number) => new Date(sec * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
/** A line a PERSON wrote in a shared chat: mine on the right, like my own messages; anybody else's
 *  on the left as a bubble with their initials and name (founder 2026-10-15). */
function PersonLine({ who, mine, repeat, seq, children }: { who: string; mine: boolean; repeat?: boolean; seq?: number; children: ReactNode }) {
  // S7 (7gg, phone pass): long-press a message → Copy reference / Share (the native sheet on a phone)
  const session = useContext(MessageScope);
  const press = longPressProps(() => { if (session && seq !== undefined) raiseMessageActions({ kind: "message", id: `${session}/${seq}` }); });
  return (
    <div data-turn={repeat ? "repeat" : "line"} className={mine ? "vx-turn vx-turn-mine" : "vx-turn vx-turn-other"} {...press}>
      {mine ? <div data-turn-who="" className="vx-sr">{who}</div>
        : <Identity kind="person" id={who} name={who} size={24} className="vx-turn-avatar" />}
      <div className="vx-turn-other-body">
        {!mine && <div data-turn-who="" className="vx-turn-who">{who}</div>}
        <div className={mine ? "vx-bubble vx-bubble-mine" : "vx-bubble vx-bubble-other"}>{children}</div>
      </div>
    </div>
  );
}

function RepeatLine({ lines, who }: { lines: RoomLine[]; who: string }) {
  const [open, setOpen] = useState(false);
  const first = lines[0], last = lines[lines.length - 1];
  return (
    <PersonLine who={who} mine={who === "You"} repeat>
      <>
        <ReferenceText text={shownWords(first.text)} />{" "}
        <span className="vx-repeat-meta">×{lines.length} · {hhmm(first.created)}–{hhmm(last.created)} · </span>
        <button type="button" data-repeat-toggle className="vx-repeat-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "hide" : "details"}
        </button>
        {open && <ul className="vx-repeat-list">{lines.map((l) => <li key={l.seq} data-repeat-at>{hhmm(l.created)}</li>)}</ul>}
      </>
    </PersonLine>
  );
}

/** "Message Valeriia and Vexa" — who a message in a shared chat reaches; null in a chat of one. */
export function composerPlaceholder(v: RoomView): string | null {
  const others = Object.entries(v.names).filter(([id]) => id !== v.you && id !== "vexa").map(([, n]) => n).filter(Boolean);
  if (!others.length) return null;
  return `Message ${others.join(", ")} and Vexa`;
}

/** The lines alone (kept for a surface that shows them outside a conversation). */
export function RoomLines({ session, onTurn }: { session: string; onTurn?: () => void }) {
  const v = useRoomLines(session, onTurn);
  if (!v.lines.length) return null;
  return (
    <div data-room-lines="" aria-label="Messages in this chat" className="vx-room-lines">
      {v.lines.slice(-12).map((l) => (
        <div key={l.seq} className="vx-room-line">
          <span className="vx-room-line-who">{roomWho(v, l.author)}</span> {l.text}
        </div>
      ))}
    </div>
  );
}

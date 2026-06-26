"use client";
/** Chat — the persistent right-rail agent window. Streams a real agent turn over /api/chat (SSE) into the
 *  turn timeline, surfacing each tool-call as a visible operation (read/search/edit/git/web) with status,
 *  then the message + commit / rejection badge. The composer carries the active center-tab reference. */
import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ClipboardEvent, type DragEvent, type ReactNode } from "react";
import { useService, useStore, CommandServiceId } from "../platform";
import { LayoutServiceId, type ActiveTab } from "../workbench/layout";
import { registerCommand, type TabProps } from "../contributions";
import { AgentWindow, Conversation, opIcon, type Turn, type Op } from "../workbench/agent-window";
import { Icon } from "../ui-kit";
import { sessionTitle, type SessionSummary } from "./sessions";
import { useLiveMeetings } from "./liveMeetings";
import { meetingById, type MeetingMock } from "./mock";
import { ASK_CHAT_EVENT } from "../canvas/actions";

/** classify a tool name into one of the op icons so the operation line reads at a glance */
function toolOp(tool: string): Op {
  const t = tool.toLowerCase();
  const icon = /read|cat|open/.test(t) ? opIcon.read : /search|grep|find/.test(t) ? opIcon.search
    : /edit|write|append/.test(t) ? opIcon.edit : /git|commit/.test(t) ? opIcon.git
    : /web|fetch|http/.test(t) ? opIcon.web : opIcon.tool;
  return { icon, label: tool, status: "done" };
}

/** the backend history turn shape (GET /api/sessions/:session/history) */
type HistoryTurn =
  | { role: "user"; text: string }
  | { role: "agent"; text: string; ops?: { label: string }[]; commit?: string };

type AgentTurn = Extract<Turn, { role: "agent" }>;
type ChatSessionState = {
  turns: Turn[];
  busy: boolean;
  loading: boolean;
  loaded: boolean;
  nextId: number;
  abort: AbortController | null;
};

const EMPTY_CHAT_STATE: ChatSessionState = { turns: [], busy: false, loading: false, loaded: false, nextId: 0, abort: null };
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
function historyOp(op: { label: string }): Op {
  return { icon: opIcon[op.label] ?? opIcon.tool, label: op.label, status: "done" };
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

function compactStoredUserText(text: string): string {
  const raw = text.trim();
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
  return text;
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
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await (await fetch(`/api/sessions?subject=${encodeURIComponent(subject)}`)).json() as { sessions?: SessionSummary[] };
        if (!cancelled) setSessions(data.sessions ?? []);
      } catch {
        if (!cancelled) setSessions([]);
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
        const data = await (await fetch(`/api/sessions?subject=${encodeURIComponent(subject)}`)).json() as { sessions?: SessionSummary[] };
        if (!cancelled) setSessions(data.sessions ?? []);
      } catch {
        if (!cancelled) setSessions([]);
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
      if (e.key === "Escape") setOpen(false);
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

function ChatConversation({ turns, busy, empty }: { turns: Turn[]; busy?: boolean; empty?: ReactNode }) {
  if (turns.length === 0 && empty) return <>{empty}</>;
  return (
    <>
      {turns.map((t, i) => t.role === "user"
        ? <div key={t.id} style={{ marginBottom: 16 }}><div style={userBubble}><ReferenceText text={t.text} /></div></div>
        : <Conversation key={t.id} turns={[t]} busy={!!busy && i === turns.length - 1} />)}
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
  return context ? `${prompt.trim()}\n\n---\n${context}` : prompt.trim();
}

function activeReference(tab: ActiveTab | null): ActiveReference | null {
  if (!tab) return null;
  const path = typeof tab.params.path === "string" ? tab.params.path : null;
  if ((tab.kind === "doc" || tab.kind === "file") && path) return { kind: "file", value: path, raw: `@file:${path}` };
  const meetingId = typeof tab.params.meetingId === "string" ? tab.params.meetingId : null;
  if (tab.kind === "meeting" && meetingId) return { kind: "meeting", value: meetingId, raw: `@meeting:${meetingId}` };
  return null;
}

function activeContextPrompt(ref: ActiveReference | null, meeting: MeetingMock | undefined): string {
  if (!ref) return "";
  if (ref.kind === "file") {
    return `Active context: the user is viewing the workspace file ${ref.value}. Read it with your Read tool if relevant.`;
  }

  const native = meeting?.native_id ?? meeting?.id ?? ref.value;
  const platform = meeting?.platform === "Google Meet" || meeting?.platform === "google_meet" ? "google_meet" : (meeting?.platform ?? "google_meet");
  const notesPath = `kg/entities/meeting/${native}.md`;
  return [
    `Active meeting reference: @meeting:${native}`,
    `Platform: ${platform}`,
    `Native id: ${native}`,
    `Notes workspace path: ${notesPath}`,
    `Transcript API path: /api/transcripts/${platform}/${native}`,
    "Instruction: use the notes path first; fetch the transcript only if the answer needs exact meeting evidence. Do not paste the full transcript into the chat.",
  ].join("\n");
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

type ChatProps = Partial<TabProps>;

export function Chat({ params = {} }: ChatProps) {
  const subject = typeof params.subject === "string" ? params.subject : "u_live";  // one workspace shared with meeting research
  const commands = useService(CommandServiceId);
  const layout = useService(LayoutServiceId);
  const { activeTab, activeSession } = useStore(layout.store);
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
  const focusRef = focusCleared ? null : activeRef;
  const meetings = useLiveMeetings();
  const activeMeeting = activeRef?.kind === "meeting"
    ? meetings.find((m) => m.id === activeRef.value || m.native_id === activeRef.value) ?? meetingById(activeRef.value)
    : undefined;
  const contextRef: ActiveReference | null = focusRef?.kind === "meeting"
    ? { kind: "meeting", value: activeMeeting?.native_id ?? activeMeeting?.id ?? focusRef.value, raw: `@meeting:${activeMeeting?.native_id ?? activeMeeting?.id ?? focusRef.value}` }
    : focusRef;
  const [uploading, setUploading] = useState(false);
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const attachmentSeqRef = useRef(0);
  const attachmentsRef = useRef<ComposerAttachment[]>([]);

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
    let cancelled = false;
    updateChatState(key, (s) => ({ ...s, loading: true }));
    (async () => {
      try {
        const r = await fetch(`/api/sessions/${encodeURIComponent(session)}/history?subject=${encodeURIComponent(subject)}`);
        const data: { turns?: HistoryTurn[] } = await r.json();
        if (cancelled) return;
        const loaded: Turn[] = (data.turns ?? []).map((t, i) =>
          t.role === "user"
            ? { id: `h-u-${i}`, role: "user", text: compactStoredUserText(t.text) }
            : { id: `h-a-${i}`, role: "agent", text: t.text, ops: (t.ops ?? []).map(historyOp), commit: t.commit });
        updateChatState(key, (s) => {
          if (s.busy || s.turns.length > 0) return { ...s, loading: false, loaded: true };
          return { ...s, turns: loaded, nextId: Math.max(s.nextId, loaded.length), loading: false, loaded: true };
        });
      } catch {
        if (!cancelled) updateChatState(key, (s) => ({ ...s, loading: false, loaded: true }));
      }
    })();
    return () => { cancelled = true; };
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
    form.append("subject", subject);
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

  const send = async (text: string, prompt = text, referenceSource = text) => {
    const v = text.trim();
    const basePrompt = promptWithReferences(prompt, referenceSource.trim());
    const key = chatKey;
    const sessionForSend = session;
    const state = getChatState(key);
    if (!v || !basePrompt || state.busy) return;
    const n = state.nextId;
    const agentId = `a-${n}`;
    const displayText = appendReferenceToken(v, contextRef);
    const ctrl = new AbortController();
    updateChatState(key, (s) => ({
      ...s,
      turns: [...s.turns, { id: `u-${n}`, role: "user", text: displayText }, { id: agentId, role: "agent", text: "", ops: [] }],
      busy: true,
      loading: false,
      loaded: true,
      nextId: Math.max(s.nextId, n + 1),
      abort: ctrl,
    }));
    try {
      const p = promptWithActiveContext(basePrompt, contextRef, activeMeeting);
      const active = contextRef ? { kind: contextRef.kind, ref: contextRef.raw } : undefined;
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: p, subject, session: sessionForSend, active }), signal: ctrl.signal });
      if (!r.ok) throw new Error(`Chat request failed (${r.status})`);
      const reader = r.body?.getReader();
      const dec = new TextDecoder();
      let buf = "";
      let sawVisibleOutput = false;
      while (reader) {
        const { value: chunk, done } = await reader.read();
        if (done) break;
        buf += dec.decode(chunk, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let ev: { type: string; text?: string; tool?: string; sha?: string; ok?: boolean; reply?: string };
          try { ev = JSON.parse(line.slice(6)); } catch { continue; }
          if (ev.type === "message-delta") {
            sawVisibleOutput = sawVisibleOutput || Boolean(ev.text);
            patchAgentTurn(key, agentId, (t) => ({ ...t, text: (t.text ?? "") + (ev.text ?? "") }));
          }
          else if (ev.type === "tool-call") {
            sawVisibleOutput = true;
            patchAgentTurn(key, agentId, (t) => ({ ...t, ops: [...t.ops, toolOp(ev.tool ?? "tool")] }));
          }
          else if (ev.type === "commit") patchAgentTurn(key, agentId, (t) => ({ ...t, commit: ev.sha }));
          else if (ev.type === "rejected") patchAgentTurn(key, agentId, (t) => ({ ...t, rejected: "workspace.v1 violation — reverted" }));
          else if (ev.type === "done" && ev.ok === false) {
            sawVisibleOutput = true;
            patchAgentTurn(key, agentId, (t) => ({
              ...t,
              text: (t.text ?? "") + (t.text ? "\n\n" : "") + `Model inference failed${ev.reply ? `: ${ev.reply}` : "."}`,
            }));
          }
        }
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
      }
      if (!sawVisibleOutput) {
        patchAgentTurn(key, agentId, (t) => ({ ...t, text: (t.text ?? "") || "No chat output arrived before the stream closed." }));
      }
    } catch (e) {
      if ((e as Error)?.name === "AbortError") patchAgentTurn(key, agentId, (t) => ({ ...t, text: (t.text ?? "") + (t.text ? "\n\n" : "") + "_stopped_" }));
      else patchAgentTurn(key, agentId, (t) => ({ ...t, text: (t.text ?? "") + (t.text ? "\n\n" : "") + ((e as Error)?.message || "Chat request failed.") }));
    } finally {
      updateChatState(key, (s) => ({ ...s, busy: false, abort: null }));
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
      const prompt = (e as CustomEvent<{ prompt?: string }>).detail?.prompt;
      if (!prompt) return;
      if (layout.store.getState().rightCollapsed) layout.toggleRight();
      void sendRef.current(prompt);
    };
    window.addEventListener(ASK_CHAT_EVENT, onAsk);
    return () => window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  }, [layout]);

  const focusInput = () => window.setTimeout(() => inputRef.current?.focus(), 0);
  const selectSession = (id: string) => { layout.setActiveSession(id); focusInput(); };
  const newChat = () => selectSession(`chat-${Date.now().toString(36)}`);

  const onSubmit = async () => {
    const v = value.trim();
    const hasAttachments = attachments.length > 0;
    if ((!v && !hasAttachments) || busy || uploading) return;
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
        {contextRef && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
            <span style={{ color: "var(--t3)", fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em", flex: "none" }}>Focus</span>
            <ReferenceChip refToken={contextRef} />
            <button aria-label="Clear focus" title="Clear focus" onClick={() => setFocusCleared(true)} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", padding: 0, marginLeft: 2, flex: "none" }}><Icon name="x" size={12} /></button>
          </div>
        )}
        <ComposerReferences text={value} />
        <AttachmentChips attachments={attachments} onRemove={removeAttachment} />
        {uploadError && <div style={{ color: "var(--danger, #ff8b8b)", fontSize: 12, lineHeight: 1.35 }}>{uploadError}</div>}
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
            placeholder="Type / for skills, or ask the agent…"
            disabled={busy || uploading}
            rows={1}
            style={{ flex: 1, background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 14, lineHeight: "20px", minWidth: 0, minHeight: 28, maxHeight: MAX_TEXTAREA_HEIGHT, resize: "none", overflowY: "hidden", padding: "4px 0", margin: 0, fontFamily: "inherit" }}
          />
          <button type="button" aria-label="Attach files" title="Attach files" disabled={busy || uploading} onClick={() => fileInputRef.current?.click()}
            style={{ background: "transparent", color: "var(--t3)", border: "1px solid var(--line2)", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: busy || uploading ? "default" : "pointer", flex: "none", opacity: busy || uploading ? 0.6 : 1 }}>
            <Icon name="paperclip" size={15} />
          </button>
          {busy
            ? <button aria-label="Stop" title="Stop" onClick={stop} style={{ background: "var(--panel2)", color: "var(--t1)", border: "1px solid var(--line2)", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", flex: "none" }}><span style={{ width: 10, height: 10, background: "var(--t1)", borderRadius: 2, display: "block" }} /></button>
            : <button aria-label="Send" disabled={uploading} onClick={() => void onSubmit()} style={{ background: "var(--accent)", color: "#241008", border: "none", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: uploading ? "default" : "pointer", flex: "none", opacity: uploading ? 0.7 : 1 }}><Icon name="send" size={16} /></button>}
        </div>
      </div>
    </>
  );

  return (
    <AgentWindow top={<ChatHeader subject={subject} session={session} onSelectSession={selectSession} onNewChat={newChat} onClose={() => layout.toggleRight()} />} scrollRef={scrollRef} composer={composer}>
      <ChatConversation turns={turns} busy={busy || loading} empty={<div style={{ color: "var(--t3)", fontSize: 13, textAlign: "center", marginTop: 40 }}>{loading ? "Loading conversation…" : "Ask the agent to record, research, or restructure knowledge — it writes to your git workspace and commits."}</div>} />
    </AgentWindow>
  );
}

registerCommand({ id: "skill.research", title: "Research and file to the workspace", skill: "/research", run: () => {} });
registerCommand({ id: "skill.draft", title: "Draft an email or doc", skill: "/draft", run: () => {} });
registerCommand({ id: "skill.routine", title: "Create a scheduled routine", skill: ROUTINE_COMMAND, run: () => {} });

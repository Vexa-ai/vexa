"use client";
/** Chat — the persistent right-rail agent window. Streams a real agent turn over /api/chat (SSE) into the
 *  turn timeline, surfacing each tool-call as a visible operation (read/search/edit/git/web) with status,
 *  then the message + commit / rejection badge. The composer carries the active center-tab reference. */
import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ClipboardEvent, type DragEvent, type ReactNode } from "react";
import { useService, useStore, CommandServiceId } from "../platform";
import { LayoutServiceId, type ActiveTab } from "../workbench/layout";
import { registerCommand, type TabProps } from "../contributions";
import { meetingsOnly } from "../app/mode";
import { AgentWindow, Conversation, opIcon, type Turn, type Op } from "../workbench/agent-window";
import { Icon } from "../ui-kit";
import { startStreamingDictation, type StreamingDictation } from "../ui-kit/micDictation";
import { sessionTitle, type SessionSummary } from "./sessions";
import { listSessions } from "./sessionsApi";
import { streamChatTurn, type ChatPhase } from "./chatStream";
import { buildChatContext, focusTarget, readIncludeSchedule, scheduleEligible, writeIncludeSchedule, type FocusPayload } from "./chatContext";
import { useLiveMeetings } from "./liveMeetings";
import { meetingPhase, type MeetingMock, type MeetingPhase } from "./meetingModel";
import { ASK_CHAT_EVENT, ONBOARDING_KICKOFF_MARK, ONBOARDING_SEED_EVENT, ONBOARDING_GREETING, ONBOARDING_GROUNDING, ONBOARDING_REPLY_SEP } from "../canvas/actions";

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

type ChatProps = Partial<TabProps>;

export function Chat({ params = {} }: ChatProps) {
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
        const loaded: Turn[] = (data.turns ?? [])
          // Drop a PURE onboarding kickoff (legacy: marker with no user reply). A grounding-wrapped reply
          // (marker + grounding + reply) is KEPT and compacted to just the reply by compactStoredUserText.
          .filter((t) => !(t.role === "user" && t.text.includes(ONBOARDING_KICKOFF_MARK) && !t.text.includes(ONBOARDING_REPLY_SEP)))
          .map((t, i) =>
            t.role === "user"
              ? { id: `h-u-${i}`, role: "user", text: compactStoredUserText(t.text) }
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

  const send = async (text: string, prompt = text, referenceSource = text, opts: { hidden?: boolean; ground?: boolean } = {}) => {
    // hidden → no visible user bubble (system kickoffs); ground:false → don't append the active
    // meeting/file context (onboarding must not inherit whatever meeting happens to be focused).
    const { hidden = false, ground = true } = opts;
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
    try {
      const result = await streamChatTurn(
        { prompt: p, session: sessionForSend, active, context },
        {
          onStarting: () => {},  // visual is driven by onStatus (below); the stream still signals cold-start here
          onStatus: (phase) => setStatus(phase),
          onDelta: (text) => patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, text: (t.text ?? "") + text })),
          onTool: (tool) => patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, ops: [...t.ops, toolOp(tool)] })),
          onCommit: (sha) => patchAgentTurn(key, agentId, (t) => ({ ...t, commit: sha })),
          onRejected: () => patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, rejected: "workspace.v1 violation — reverted" })),
          onModelFailure: (reply) => patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, text: (t.text ?? "") + (t.text ? "\n\n" : "") + `Model inference failed${reply ? `: ${reply}` : "."}` })),
          onError: (msg) => patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, text: (t.text ?? "") + (t.text ? "\n\n" : "") + msg })),
          onProgress: () => { if (stickToBottomRef.current) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); },
        },
        { signal: ctrl.signal },
      );
      if (!result.aborted && !result.terminal) {
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
        patchAgentTurn(key, agentId, (t) => ({ ...t, status: null }));  // clean end — drop any lingering status line
      }
    } catch (e) {
      if ((e as Error)?.name === "AbortError") patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, text: (t.text ?? "") + (t.text ? "\n\n" : "") + "_stopped_" }));
      else patchAgentTurn(key, agentId, (t) => ({ ...t, status: null, text: (t.text ?? "") + (t.text ? "\n\n" : "") + ((e as Error)?.message || "Chat request failed.") }));
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
      const detail = (e as CustomEvent<{ prompt?: string; hidden?: boolean; ground?: boolean }>).detail;
      const prompt = detail?.prompt;
      if (!prompt) return;
      if (layout.store.getState().rightCollapsed) layout.toggleRight();
      void sendRef.current(prompt, prompt, prompt, { hidden: detail?.hidden, ground: detail?.ground });
    };
    window.addEventListener(ASK_CHAT_EVENT, onAsk);
    return () => window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  }, [layout]);

  // Onboarding seeds a CACHED greeting (instant, no LLM) and arms the chat — the user's next reply carries
  // the discovery-loop grounding (applied in onSubmit), so the agent starts researching from one answer.
  const onboardingArmedRef = useRef(false);
  useEffect(() => {
    const onSeed = () => {
      if (layout.store.getState().rightCollapsed) layout.toggleRight();
      const key = chatKey;
      updateChatState(key, (s) => (
        s.turns.length
          ? { ...s, loaded: true, loading: false }
          : { ...s, turns: [{ id: "onb-greeting", role: "agent", text: ONBOARDING_GREETING, ops: [] }], nextId: Math.max(s.nextId, 1), loaded: true, loading: false }
      ));
      onboardingArmedRef.current = true;
    };
    window.addEventListener(ONBOARDING_SEED_EVENT, onSeed);
    return () => window.removeEventListener(ONBOARDING_SEED_EVENT, onSeed);
  }, [layout, chatKey]);

  const focusInput = () => window.setTimeout(() => inputRef.current?.focus(), 0);
  const selectSession = (id: string) => { layout.setActiveSession(id); focusInput(); };
  const newChat = () => selectSession(`chat-${Date.now().toString(36)}`);

  const onSubmit = async () => {
    const v = value.trim();
    const hasAttachments = attachments.length > 0;
    if ((!v && !hasAttachments) || busy || uploading) return;
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
    // First onboarding reply: prepend the (hidden) discovery-loop grounding so the agent researches from
    // this one answer. compactStoredUserText strips it back off on reload; the user only ever sees `displayText`.
    if (onboardingArmedRef.current && !hasAttachments) {
      onboardingArmedRef.current = false;
      prompt = ONBOARDING_GROUNDING + ONBOARDING_REPLY_SEP + prompt;
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
        {(contextRef || ambientEligible || includeSchedule === true || (bundleFocus && (bundleFocus.kind === "workspace" || bundleFocus.kind === "today"))) && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, flexWrap: "wrap" }}>
            {/* ambient schedule chip — the context bundle's always-visible half: on = the agent
                sees today's schedule; × turns it off for this session; ghost chip re-adds */}
            {ambientOn ? (
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
            {contextRef && contextRef.kind === "meeting" && activeMeeting && (() => {
              const mode = MODE_CHIP[meetingPhase(activeMeeting)];
              return (
                <span title={`This chat is grounded in the meeting's ${mode.label.toLowerCase()} state`}
                  style={{ flex: "none", display: "inline-flex", alignItems: "center", gap: 5, fontFamily: "var(--mono)",
                    fontSize: 10, letterSpacing: ".07em", textTransform: "uppercase", color: mode.color,
                    background: mode.bg, borderRadius: 999, padding: "2px 9px", maxWidth: 220 }}>
                  {mode.label} · <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textTransform: "none", letterSpacing: 0 }}>{meetingLabel(activeMeeting)}</span>
                </span>
              );
            })()}
            {contextRef && (
              <>
                <span style={{ color: "var(--t3)", fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em", flex: "none" }}>Focus</span>
                <ReferenceChip refToken={contextRef} />
                <button aria-label="Clear focus" title="Clear focus" onClick={() => setFocusCleared(true)} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", padding: 0, marginLeft: 2, flex: "none" }}><Icon name="x" size={12} /></button>
              </>
            )}
            {!contextRef && bundleFocus && (bundleFocus.kind === "workspace" || bundleFocus.kind === "today") && (
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
          {busy
            ? <button aria-label="Stop" title="Stop" onClick={stop} style={{ background: "var(--panel2)", color: "var(--t1)", border: "1px solid var(--line2)", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", flex: "none" }}><span style={{ width: 10, height: 10, background: "var(--t1)", borderRadius: 2, display: "block" }} /></button>
            : <button aria-label="Send" disabled={uploading} onClick={() => void onSubmit()} style={{ background: "var(--accent)", color: "var(--on-accent)", border: "none", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: uploading ? "default" : "pointer", flex: "none", opacity: uploading ? 0.7 : 1 }}><Icon name="send" size={16} /></button>}
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

// Agent /-skills — absent in meetings-only mode (NEXT_PUBLIC_TERMINAL_MODE=meetings), where the chat
// rail itself doesn't render (Workbench) and the proxy refuses agent paths.
if (!meetingsOnly()) {
  registerCommand({ id: "skill.research", title: "Research and file to the workspace", skill: "/research", run: () => {} });
  registerCommand({ id: "skill.draft", title: "Draft an email or doc", skill: "/draft", run: () => {} });
  registerCommand({ id: "skill.routine", title: "Create a scheduled routine", skill: ROUTINE_COMMAND, run: () => {} });
}

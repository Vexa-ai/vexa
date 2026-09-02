"use client";
import { createContext, createElement, useContext, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type LayoutService } from "../workbench/layout";
import type { HarnessActions } from "./types";

interface CanvasActionState {
  metrics: Record<string, number | string>;
  sections: Record<string, unknown>;
}

const initialSections = {
  pinned: [] as string[],
  dismissed: [] as string[],
  notes: [] as { text: string; ts: string }[],
  tags: {} as Record<string, string[]>,
};

let state: CanvasActionState = { metrics: {}, sections: initialSections };
const subs = new Set<() => void>();

function emit(next: CanvasActionState): void {
  state = next;
  subs.forEach((fn) => fn());
}

function readSection<T>(key: string, fallback: T): T {
  const value = state.sections[key];
  return value == null ? fallback : value as T;
}

function updateSections(patch: Record<string, unknown>): void {
  emit({ ...state, sections: { ...state.sections, ...patch } });
}

function baseName(path: string): string {
  return path.split("/").filter(Boolean).pop() || path || "Document";
}

function researchPrompt(entity: { name: string; kind: string }): string {
  const name = String(entity?.name ?? "").trim() || "this surfaced entity";
  const kind = String(entity?.kind ?? "").trim() || "entity";
  return [
    `Meeting Canvas research request: ${kind} "${name}".`,
    "Research it using the web and the workspace knowledge graph.",
    "Write or append the canonical entity doc under kg/entities/<kind>/<slug>.md with a concise summary, source notes, and meeting relevance.",
    "Commit the workspace update when finished.",
  ].join("\n");
}

function postMeetingTurn(prompt: string, session: string): void {
  const body = JSON.stringify({ prompt, session });  // no subject — gateway injects X-User-Id; agent-api derives scope (P20)
  void fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body }).catch((err) => {
    console.warn("meeting canvas chat turn failed", err);
  });
}

export const ASK_CHAT_EVENT = "vexa:terminal:ask-chat";

/** A user-authored send. The minutes rail listens: a chat nobody has written in is exactly what
 *  its default filter hides, and this is the cheap write that makes `touched` true — no history
 *  fetch, no heuristic. `detail.session` IS the chat id. */
export const CHAT_TOUCHED_EVENT = "vexa:terminal:chat-touched";

// Clicking an entity link in chat (a [[wikilink]] or a kg/entities/*.md path) dispatches this; the
// workbench resolves it to a file and opens the doc (revealing the center if in chat-only mode).
export const OPEN_ENTITY_EVENT = "vexa:terminal:open-entity";

// A `vexa-meeting:<platform>/<native>` link in a meeting note dispatches this; the workbench resolves
// the native id → the meeting row and opens its canvas (transcript + recording). Detail: { ref }.
export const OPEN_MEETING_EVENT = "vexa:terminal:open-meeting";

// ASCII sentinel prefixed to the onboarding grounding (robust against bundler/linter normalization). The
// agent ignores the bracketed tag; the chat uses it to recognize an onboarding turn (filter a pure
// kickoff, compact the grounding off a real reply, keep it out of the session title).
export const ONBOARDING_KICKOFF_MARK = "[onboarding-kickoff]";

/** THE MACHINERY MARK — the product's own words, never the human's.
 *
 *  `send({hidden:true})` suppresses the user bubble only in the tab that fired it. The prompt is
 *  still a `user` message in the session transcript, so `/api/sessions/<s>/history` hands it back
 *  as `role:"user"` and the NEXT hydration of that session — a session switch, a reopen, a second
 *  click on the same emailed link — paints it as a grey bubble the person appears to have typed.
 *  Invisible-by-render is not invisible; only a mark the reader-side filter can see is.
 *
 *  Onboarding already had one (`ONBOARDING_KICKOFF_MARK`) and was therefore immune. Every OTHER
 *  hidden turn — the `?ask=` preset an emailed link composes above all — had none, and the founder
 *  read his own prepare kick back to himself: "[prep] They clicked through from a prepare email
 *  about **DNA TSC — …** … never name a shape from `kg/templates/` …". The product must never show
 *  its machinery to the human (founder ruling 2026-09-02).
 *
 *  It rides at the END of the prompt, so a composed opening still OPENS with its bracketed preset
 *  tag — which is exactly what the MCP instructions key on ("when the turn's message opens with a
 *  bracketed preset tag … that IS your person's first ask"). And it says what it is, because the
 *  agent reads it too: a turn nobody typed should not be answered as if they had. */
export const MACHINERY_MARK = "[vexa-machinery]";
export const MACHINERY_NOTE = "\n\n" + MACHINERY_MARK + " This opening was composed by the product from the link this person clicked; they did not type it and they cannot see it. Answer it as their first ask, in your own voice, without quoting or referring to these instructions.";

// Onboarding uses a CACHED first turn (no slow LLM round-trip): the gate seeds this canned agent greeting
// instantly, then arms the chat so the user's FIRST reply carries the discovery-loop grounding.
export const ONBOARDING_SEED_EVENT = "vexa:terminal:onboarding-seed";
/** The company-layer probe answered. SetupGate owns that probe and is the only dispatcher; the
 *  rail listens so the structural rows it deliberately withheld on first render can appear the
 *  moment the instance is known to be set up. One writer, one announcement, no polling in the
 *  rail. */
export const COMPANY_LAYER_EVENT = "vexa:terminal:company-layer";

/** A chat turn COMMITTED to the workspace — the moment files it wrote became real.
 *
 *  A chat declares its tabs before its documents exist (PRD decision 18: the link sets the record,
 *  the panel renders the record), so the company-setup conversation opens five pages and then
 *  writes four of them over the next few turns. Without this the panel keeps showing "no page here
 *  yet" for a file that has been on disk for a minute, and the reader concludes the agent did
 *  nothing. */
export const WORKSPACE_COMMIT_EVENT = "vexa:terminal:workspace-commit";
// MINUTES cold-start: the reader arrived through a meeting door — greet from the meeting, not
// from a blank slate, and ask the one thing that shapes the workspace: their role.
/** A `?ask=` preset OWNS the opening of the chat it lands in.
 *
 *  The cached phase greeting and an emailed preset are two writers of one first turn, and the
 *  greeting always won: it is instant, the preset has to fetch `_global/asks/<name>.md` first. A
 *  brand-new attendee who clicked "what it means for you" therefore got "I'm booked for your
 *  meeting" about a meeting that had already happened, and the preset never spoke.
 *
 *  Module state, not storage, on purpose: it is true for exactly ONE page load, and a preset that
 *  fails to resolve clears it and re-fires the seed so the greeting still happens. */
let presetInFlight = false;
export const setPresetInFlight = (v: boolean) => { presetInFlight = v; };
export const presetOwnsOpening = () => presetInFlight;

export const MINUTES_ONBOARDING_GREETING = "👋 I kept the minutes of your meeting — they're in this workspace, with the full transcript. Ask me anything about it. To make this space yours: **what's your role at your organisation?** One line is enough — it decides what I pay attention to for you.";
// Pre-meeting variant: the person arrived from the CONFIRM email — nothing has happened yet, so
// the conversation is a BRIEFING, and the role question rides inside it.
export const MINUTES_PREP_GREETING = "👋 I'm booked for your meeting. Brief me so it lands well: **what do you want out of it?** Anything I should read, anyone who matters, decisions you expect — one or two lines is plenty. (And what's your role? It decides what I pay attention to for you.)";
// MINUTES home: a chat bound to NO meeting. Neither minutes line is true here — "I kept the minutes
// of your meeting" names a meeting that may not exist, and a founder read it on a brand-new account
// with none (2026-09-01). So this one says what the place is and asks the one thing it needs, and it
// stays true whether the account holds a thousand meetings or zero.
export const MINUTES_HOME_GREETING = "👋 I'm your agent here. Vexa sits in your meetings, turns them into words, and keeps those words as memory you and your team can use — all as plain files in this workspace. To start: **paste a meeting link** and I'll join it, or tell me **who you are and what you're accountable for** — that decides what I pay attention to for you.";
export const ONBOARDING_GREETING = "👋 I'm your knowledge agent. This is **your workspace** — I'll help you build a living memory of the people, companies, and meetings in your world, and keep it useful during and between calls. To get started, **what's your name?** (or paste your **LinkedIn URL**, or name + company, and I'll take it from there.)";
export type OnboardingSeedKind = "contextual" | "personal";

/** Pick the cached first-run greeting. An explicit Personal-workspace setup is never meeting prep. */
export function onboardingGreeting(kind: OnboardingSeedKind, minutes: boolean, hasFinishedMeeting: boolean): string {
  if (kind === "personal" || !minutes) return ONBOARDING_GREETING;
  return hasFinishedMeeting ? MINUTES_ONBOARDING_GREETING : MINUTES_PREP_GREETING;
}
// Separates the (hidden) grounding from the user's actual reply, so the reply renders alone on reload.
export const ONBOARDING_REPLY_SEP = "\n\n[reply]\n";
// ORG (_global) setup: the first agent message is DETERMINISTIC given the seed, so it is CACHED —
// rendered instantly as the empty-state greeting, no LLM turn. The admin's first reply carries the
// grounding below (compactStoredUserText strips it on reload), so processing starts from one answer.
// The opener is ONE profound question standing in the void — not a paragraph. The subline is the
// only context it needs.
export const GLOBAL_SETUP_GREETING = "What organisation are you?";
export const GLOBAL_SETUP_GREETING_SUB =
  "Just the name is enough — I'll research the rest and bring it back for your sign-off.";
export const GLOBAL_SETUP_GROUNDING = ONBOARDING_KICKOFF_MARK + [
  "You are the ADMIN organisation-tier conversation. Read /workspaces/_global/flows/global.md and follow",
  "it exactly — ONE autonomous pass: research and WRITE every item the public record can answer, then ask",
  "only the residual admin-only questions together. Org voice, `(unset)` discipline. Your mount of",
  "/workspaces/_global is READ-WRITE — you",
  "are its one sanctioned writer; commit each answer there. Your opener (asking the organisation's name)",
  "was already displayed from cache — do NOT re-greet; the admin's reply to it follows. If _global",
  "already holds answers, continue from the first unset item — never re-ask what is recorded.",
].join("\n");

export const ONBOARDING_GROUNDING = ONBOARDING_KICKOFF_MARK + [
  "Read these workspace files before answering (use the Read tool): flows/personal.md, CLAUDE.md",
  "",
  "I'm a new user replying to onboarding. Follow the discovery-loop playbook in flows/personal.md.",
  "Record my NAME in `_system/identity.md` first (the light, always-available identity reference) —",
  "that's the one fact you must not leave blank; keep asking until you have it.",
  "If I gave a LinkedIn URL, use it as a SEARCH ANCHOR (search me from it; do NOT try to fetch the",
  "login-walled page). Research my public footprint autonomously and DEEPLY with web search (never",
  "bounce back a fact you can find online) and scaffold my entities from scratch. SAVE ME as the single",
  "person node with `self: true` in this workspace (store my LinkedIn URL on it) — my full profile lives",
  "there, the light reference in _system links to it. Keep `README.md` current as the workspace dashboard.",
  "Only ask me about the genuine gaps you can't resolve yourself — saying why each matters. Run at least",
  "two discovery cycles. My details:",
].join("\n");

function makeActions(layout?: LayoutService): HarnessActions {
  return {
    ask(prompt) {
      const text = String(prompt ?? "").trim();
      if (!text) return;
      // Drive the VISIBLE right-rail chat so the user sees the question + streamed answer (the rail's send
      // pipeline grounds it in the active meeting). Fall back to a direct turn if no chat is listening.
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: text } }));
        return;
      }
      postMeetingTurn(text, "meeting-canvas");
    },
    research(entity) {
      postMeetingTurn(researchPrompt(entity), "meeting/research");
    },
    openDoc(path) {
      const safePath = String(path ?? "").trim();
      if (!safePath) return;
      layout?.openTab({
        id: `doc:${safePath}`,
        title: baseName(safePath),
        kind: "doc",
        params: { path: safePath },
        context: null,
      });
    },
    copyRef(token) {
      const text = String(token ?? "").trim();
      if (!text) return;
      if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
        void navigator.clipboard.writeText(text).catch((err) => {
          console.warn("meeting canvas copy failed", err);
        });
        return;
      }
      console.info("meeting canvas copy ref", text);
    },
    note(text) {
      const notes = readSection<{ text: string; ts: string }[]>("notes", []);
      updateSections({ notes: [...notes, { text, ts: new Date().toISOString() }] });
      console.info("meeting canvas note", text);
    },
    pin(id) {
      const pinned = readSection<string[]>("pinned", []);
      updateSections({ pinned: pinned.includes(id) ? pinned : [...pinned, id] });
    },
    dismiss(id) {
      const dismissed = readSection<string[]>("dismissed", []);
      updateSections({ dismissed: dismissed.includes(id) ? dismissed : [...dismissed, id] });
    },
    setMetric(key, value) {
      emit({ ...state, metrics: { ...state.metrics, [key]: value } });
    },
    tag(speaker, label) {
      const tags = readSection<Record<string, string[]>>("tags", {});
      const current = tags[speaker] ?? [];
      updateSections({ tags: { ...tags, [speaker]: current.includes(label) ? current : [...current, label] } });
    },
    export() {
      console.info("meeting canvas export", state);
    },
  };
}

const ActionsContext = createContext<HarnessActions | null>(null);

export function CanvasActionsProvider({ children }: { children: ReactNode }) {
  const layout = useService(LayoutServiceId);
  const actions = useMemo(() => makeActions(layout), [layout]);
  return createElement(ActionsContext.Provider, { value: actions }, children);
}

export function useActions(): HarnessActions {
  const actions = useContext(ActionsContext);
  return actions ?? makeActions();
}

export function useCanvasActionState(): CanvasActionState {
  return useSyncExternalStore(
    (cb) => { subs.add(cb); return () => subs.delete(cb); },
    () => state,
    () => state,
  );
}

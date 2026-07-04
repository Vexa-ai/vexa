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

// Clicking an entity link in chat (a [[wikilink]] or a kg/entities/*.md path) dispatches this; the
// workbench resolves it to a file and opens the doc (revealing the center if in chat-only mode).
export const OPEN_ENTITY_EVENT = "vexa:terminal:open-entity";

// ASCII sentinel prefixed to the onboarding grounding (robust against bundler/linter normalization). The
// agent ignores the bracketed tag; the chat uses it to recognize an onboarding turn (filter a pure
// kickoff, compact the grounding off a real reply, keep it out of the session title).
export const ONBOARDING_KICKOFF_MARK = "[onboarding-kickoff]";

// Onboarding uses a CACHED first turn (no slow LLM round-trip): the gate seeds this canned agent greeting
// instantly, then arms the chat so the user's FIRST reply carries the discovery-loop grounding.
export const ONBOARDING_SEED_EVENT = "vexa:terminal:onboarding-seed";
export const ONBOARDING_GREETING = "👋 I'm your knowledge agent. Your workspace starts from the **FINOS** knowledge graph — I'll add **you** on top of it. To set you up, **paste your LinkedIn URL** (just the link — that's how you position who you are). Or give me your name + company and I'll take it from there.";
// Separates the (hidden) grounding from the user's actual reply, so the reply renders alone on reload.
export const ONBOARDING_REPLY_SEP = "\n\n[reply]\n";
export const ONBOARDING_GROUNDING = ONBOARDING_KICKOFF_MARK + [
  "Read these workspace files before answering (use the Read tool): onboarding.md, CLAUDE.md",
  "",
  "I'm a new user replying to onboarding. Follow the discovery-loop playbook in onboarding.md:",
  "this workspace ALREADY holds the FINOS knowledge graph — add ME on top of it, don't start blank.",
  "If I gave a LinkedIn URL, use it as a SEARCH ANCHOR (search me from it; do NOT try to fetch the",
  "login-walled page). Research my public footprint autonomously and DEEPLY with web search (never",
  "bounce back a fact you can find online) and scaffold my entities. SAVE ME as the single person node",
  "with `self: true` (store my LinkedIn URL on it) so I'm distinct from the FINOS people and everyone",
  "else. Only ask me about the genuine gaps you can't resolve yourself — saying why each matters. Run",
  "at least two discovery cycles. My details:",
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

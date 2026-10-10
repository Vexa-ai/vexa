"use client";
import { createContext, createElement, useContext, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { useService, ASK_CHAT_EVENT } from "../platform";
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

// MINUTES cold-start: the reader arrived through a meeting door — greet from the meeting, not
// from a blank slate, and ask the one thing that shapes the workspace: their role.
// ── DELETED 2026-09-02 (F36): presetInFlight / setPresetInFlight / presetOwnsOpening ─────────
//
//  A `?ask=` preset and the cached phase greeting were two writers of one first turn, and the
//  greeting always won — it was instant, the preset had to fetch `_global/asks/<name>.md` first. So
//  a preset claimed the opening synchronously and the seed listener stood down when it saw the flag.
//
//  There is no second writer any more: the greeting is deleted, and a chat with no preset simply
//  opens empty. A flag with no reader is a flag that goes stale in silence, so it goes with the
//  listener that read it rather than staying as a call every arrival still has to remember to make.

export const MINUTES_ONBOARDING_GREETING = "👋 I kept the minutes of your meeting — they're in this workspace, with the full transcript. Ask me anything about it. To make this space yours: **what's your role at your organisation?** One line is enough — it decides what I pay attention to for you.";
// Pre-meeting variant: the person arrived from the CONFIRM email — nothing has happened yet, so
// the conversation is a BRIEFING, and the role question rides inside it.
export const MINUTES_PREP_GREETING = "👋 I'm booked for your meeting. Brief me so it lands well: **what do you want out of it?** Anything I should read, anyone who matters, decisions you expect — one or two lines is plenty. (And what's your role? It decides what I pay attention to for you.)";
// ── DELETED 2026-09-02 (F36): MINUTES_HOME_GREETING, ONBOARDING_GREETING, onboardingGreeting() ──
//
//  The home greeting opened every chat that was not about a meeting — "I'm your agent here … paste a
//  meeting link … tell me who you are and what you're accountable for". The founder met it in a chat
//  he had never created and said plainly that he did not like the text. It was a DEFAULT: nothing in
//  anyone's state produced it, it simply filled a blank page.
//
//  The two lines below survive because a MEETING produces them — the room is about something, and
//  what it says is true of that thing. Everything that greeted a chat about nothing is gone, and so
//  is the chooser that picked between them, because the seed that called it is gone too.

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

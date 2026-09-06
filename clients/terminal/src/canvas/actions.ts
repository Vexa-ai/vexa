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

// ── DELETED 2026-09-02: ONBOARDING_SEED_EVENT and COMPANY_LAYER_EVENT ────────────────────────
//
//  The first seeded a CACHED greeting into a brand-new chat; the second told the rail when to plant
//  its two structural rows. The founder opened a rail holding three chats he never made, greeted by
//  text he never asked for: *"where is it coming from? i did not create this chat, and i do not like
//  this text."* Both events existed only to make those two things happen, so both are gone rather
//  than left dangling with no dispatcher — a listener nobody fires is the stale-code shape he ruled
//  on in the same session (F37). A chat opened with `+` shows an empty composer and nothing else.

/** A FILE THE TURN JUST WROTE, offered to the pages panel (F41).
 *
 *  The chat surface hears it on the stream and re-emits it here rather than opening anything
 *  itself: the tab set is part of the CHAT RECORD (PRD decision 18), the minutes shell is that
 *  record's one writer, and a second opener would be a second writer of the same surface. Same
 *  seam, and for the same reason, as OPEN_ENTITY_EVENT. */
export const ARTIFACT_EVENT = "vexa:terminal:artifact";

/** SOMEBODY ASKED TO SEE SOMETHING — a successful `open_page` (Vexa-ai/vexa#1586).
 *
 *  Deliberately NOT the same event as ARTIFACT_EVENT, though both end in the same view slot. An
 *  artifact is the TURN saying "I wrote this", which must stand down in front of a reader who has
 *  opened something else; this is the READER'S OWN ASK coming back, so it always wins. Folding the
 *  two would mean one flag deciding both, and the flag would be wrong for one of them.
 *
 *  Same seam and same reason as ARTIFACT_EVENT otherwise: the chat surface hears it on the stream
 *  and re-emits it rather than opening anything itself, because the panel's state is part of the
 *  chat record (PRD decision 18) and the minutes shell is that record's one writer. */
export const OPEN_PAGE_EVENT = "vexa:terminal:open-page";

/** A WORKSPACE THE TURN CREATED, JOINING THE CHAT'S FOCUS (Vexa-ai/vexa#1603).
 *
 *  The founder asked for *"a new workspace where we will collect everything we know about ILM"*,
 *  got one, and was told *"the new workspace isn't in my native mount stack (it's reached via the
 *  workspace_* tools)"* — *"not native workspace??"*. Creating a place IS bringing it into the
 *  room, so the create moves the chip and the panel, exactly as a send moves the transcript.
 *
 *  A THIRD event rather than an artifact or an open, because it names a WORKSPACE and those two
 *  name a PAGE: the shell's answer here is to widen the chat's mount set, not to front a document.
 *  Same seam as both otherwise — the chat surface hears it on the stream and re-emits it, and the
 *  minutes shell, the one writer of the chat record (PRD decision 18), decides what happens. */
export const FOCUS_WORKSPACE_EVENT = "vexa:terminal:focus-workspace";

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
// Separates the (hidden) grounding from the user's actual reply, so the reply renders alone on reload.
export const ONBOARDING_REPLY_SEP = "\n\n[reply]\n";
// ── DELETED 2026-09-02 (F36/F37): the PRE-SCAFFOLD org-setup path ────────────────────────────
//
//  `GLOBAL_SETUP_GREETING` ("What organisation are you?"), its subline ("Just the name is enough —
//  I'll research the rest and bring it back for your sign-off") and `GLOBAL_SETUP_GROUNDING` were
//  the admin onboarding as it worked BEFORE scaffolds: the client cached the opener and attached the
//  flow grounding to the admin's first reply, keyed on a session id — `org-setup` — that only the
//  rail's own seeding ever produced.
//
//  The founder saw that card in a chat he never made, promising a research step that does not exist:
//  *"I explain this as stale code."* The admin conversation is a SCAFFOLD now (`kind: "admin-setup"`,
//  minted by /api/auth/claim-admin, opening text substituted server-side), and a scaffolded chat is
//  titled and opened by its record. So this whole path is deleted rather than left unreachable —
//  with the seeding gone, nothing could construct an `org-setup` session to reach it anyway, and a
//  branch that can only be entered by a bug is a bug waiting for its second chance.

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

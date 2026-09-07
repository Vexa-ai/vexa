/** A WORKSPACE CREATED FROM A CHAT JOINS THAT CHAT (Vexa-ai/vexa#1603) — the client half.
 *
 *  Founder walk, 2026-09-06. He asked for *"a new workspace where we will collect everything we
 *  know about ILM"*; the agent made it and then had to tell him:
 *
 *      *"The new workspace isn't in my native mount stack (it's reached via the workspace_* tools).
 *      Let me seed it via `entity_upsert`, which writes into the target workspace."*
 *
 *      — *"not native workspace??"*
 *
 *  Creating a place IS bringing it into the room. The server half — the session record, and the
 *  mount generation that gets the next turn a container with it bound read-write — is pinned in
 *  `core/agent/tests/test_chat_workspace_focus.py`. What can break quietly on THIS side is the
 *  reader dropping an event it does not know, and the chip not showing what the chat is over.
 *
 *  The second half of the same walk is in here too, because it is the same bubble: the terminal's
 *  own *"Active context: the user is viewing the workspace file README.md…"* narration rendered
 *  INSIDE his message. Only the words after the `---` were his.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { streamChatTurn, type ChatStreamCallbacks } from "../chatStream";
import { historyUserText, promptWithActiveContext } from "../chat";
import { focusSet, IMPLICIT_MOUNTS } from "../../minutes/ContextBar";

// ── the event on the wire ────────────────────────────────────────────────────────────────────────

function sseResponse(chunks: string[]): Response {
  const enc = new TextEncoder();
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(enc.encode(chunks[i++]));
      else controller.close();
    },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}

const ev = (o: Record<string, unknown>) => `data: ${JSON.stringify(o)}\n\n`;

function recorder() {
  const state = {
    focused: [] as { workspace: string; name: string }[],
    artifacts: [] as { workspace: string; path: string; focus: boolean; pin: boolean }[],
    opened: [] as { workspace: string; path: string; target: string }[],
  };
  const cb: ChatStreamCallbacks = {
    onStarting: () => {}, onDelta: () => {}, onTool: () => {}, onCommit: () => {},
    onRejected: () => {}, onModelFailure: () => {}, onError: () => {},
    onArtifact: (a) => { state.artifacts.push(a); },
    onOpen: (o) => { state.opened.push(o); },
    onFocusWorkspace: (f) => { state.focused.push(f); },
  };
  return { state, cb };
}

const noWait = { now: () => 0, sleep: async () => {}, reconnectBackoffMs: 0 };

async function run(chunks: string[]) {
  const { state, cb } = recorder();
  const fetchImpl = (async () => sseResponse(chunks)) as unknown as typeof fetch;
  await streamChatTurn({ prompt: "a new workspace for everything we know about ILM",
                         session: "pchat-abc", active: null },
    cb, { ...noWait, fetchImpl, signal: new AbortController().signal });
  return state;
}

describe("the focus event on the chat stream", () => {
  it("forwards the workspace the create made, and its human name", async () => {
    const s = await run([
      ev({ type: "focus", workspace: "industrial-light-magic-4040f4",
           name: "Industrial Light and Magic" }),
      ev({ type: "turn-complete" }),
    ]);
    expect(s.focused).toEqual([{ workspace: "industrial-light-magic-4040f4",
                                 name: "Industrial Light and Magic" }]);
  });

  it("carries an empty name rather than inventing one", async () => {
    const s = await run([ev({ type: "focus", workspace: "grp-abc" }), ev({ type: "turn-complete" })]);
    expect(s.focused).toEqual([{ workspace: "grp-abc", name: "" }]);
  });

  it("is NOT an artifact and NOT an open — it names a place, not a page", async () => {
    // The other two end in the panel's view slot and answer "which document is in front". This one
    // answers "what is this chat over", and folding it into either would put the wrong question's
    // rules (a reader's chosen focus, a pin) in charge of a mount set.
    const s = await run([
      ev({ type: "focus", workspace: "grp-abc", name: "ILM" }),
      ev({ type: "turn-complete" }),
    ]);
    expect(s.artifacts).toEqual([]);
    expect(s.opened).toEqual([]);
  });

  it("ignores a focus that names no workspace", async () => {
    const s = await run([ev({ type: "focus", name: "ILM" }), ev({ type: "turn-complete" })]);
    expect(s.focused).toEqual([]);
  });

  it("honours one a background job produced, and only the job it started", async () => {
    // Create runs as a background job (#1584). The chat it was asked in is still the chat that made
    // the place; a foreign job on the shared Stream is not this connection's business.
    const s = await run([
      ev({ type: "job-started", job_id: "j1", kind: "create", target: "ILM", line: "on it" }),
      ev({ type: "focus", job_id: "j1", workspace: "grp-ilm", name: "ILM" }),
      ev({ type: "focus", job_id: "OTHER", workspace: "grp-someone-else" }),
      ev({ type: "job-done", job_id: "j1", line: "done" }),
      ev({ type: "turn-complete" }),
    ]);
    expect(s.focused.map((f) => f.workspace)).toEqual(["grp-ilm"]);
  });
});

// ── what the chip shows once it has joined ───────────────────────────────────────────────────────

describe("the header chip is the chat's focus", () => {
  it("shows a workspace the turn created, beside what the chat already had", () => {
    expect(focusSet(["personal", "_global", "industrial-light-magic-4040f4"]))
      .toEqual(["personal", "industrial-light-magic-4040f4"]);
  });

  it("…and never the two that are mounted in every chat", () => {
    // A constant is not information (founder ruling 2026-09-01) — and a create must not put one on
    // screen by arriving through a different door.
    expect(focusSet(IMPLICIT_MOUNTS)).toEqual([]);
  });
});

describe("the shell widens THIS chat's focus on the event", () => {
  /** The listener is wiring rather than a decision, so what is pinned is that it exists and that it
   *  goes through the ONE writer of the focus set — the same function the chip's `+` calls. A second
   *  writer here is how the chip, the record and the server's mount set would come to disagree. */
  const shell = readFileSync(join(__dirname, "..", "..", "minutes", "MinutesShell.tsx"), "utf8");

  it("subscribes to the event", () => {
    expect(shell).toContain("FOCUS_WORKSPACE_EVENT");
    expect(shell).toMatch(/addEventListener\(FOCUS_WORKSPACE_EVENT/);
  });

  it("adds the workspace through setWorkspaces, and never twice", () => {
    expect(shell).toMatch(/setWorkspacesRef\.current\(\(ws\) => \(ws\.includes\(wid\) \? ws : \[\.\.\.ws, wid\]\)\)/);
  });
});

// ── the bubble shows the person's words and nothing the composer wrote ───────────────────────────

const FILE_REF = { kind: "file" as const, value: "README.md", raw: "@file:README.md" };
const TYPED = "collect all the knowledge from all sources we have into this new one";

describe("a typed message's bubble is the person's words alone", () => {
  const composed = promptWithActiveContext(TYPED, FILE_REF, undefined);

  it("still tells the model what the reader has open", () => {
    // The narration is not the defect and removing it would leave the agent knowing less than it
    // does today (`surfaceSync.ts`: the server-side surface record is NOT shipped).
    expect(composed).toContain("Active context: the user is viewing the workspace file README.md");
    expect(composed.endsWith(TYPED)).toBe(true);
  });

  it("marks where the person's words begin, so the bubble can be exact", () => {
    // The server's own sentinel goes in front of this WHOLE string, so the narration sat on the
    // human side of the only boundary there was — and the worker records that side verbatim as
    // `user_text`. Both readers take the LAST sentinel; this is that one.
    const SENTINEL = "<!--vexa:user-input-below-->";
    expect(composed).toContain(SENTINEL);
    expect(composed.slice(composed.lastIndexOf(SENTINEL) + SENTINEL.length)).toBe(TYPED);
  });

  it("renders as the sentence he typed, not the narration in front of it", () => {
    const grounding = "## Your mounted workspaces\n\ntier list...\n\n<!--vexa:user-input-below-->";
    expect(historyUserText({ text: grounding + composed })).toBe(TYPED);
  });

  it("leaves a turn with nothing in front of it exactly as it was", () => {
    expect(promptWithActiveContext(TYPED, null, undefined)).toBe(TYPED);
  });
});

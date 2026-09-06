/** F66 — A RUNNING TURN MUST LOOK LIKE ONE.
 *
 *  Founder, watching an 18-step `entity_upsert` turn: *"i know it's working now, but it just stays
 *  like it's stale — need to update animations or work etc."*
 *
 *  The cause was one word. Every op was appended with `status: "done"`, so the step line rendered a
 *  green TICK from the first tool call and never moved again — an eighteen-step turn looked
 *  finished eighteen times. These pin the corrected reducer end to end against a scripted stream:
 *  what the ops look like WHILE it runs, and what they look like when it stops.
 *
 *  F66's OTHER half — the same line repeated in the composer — was REVERSED on 2026-09-06
 *  (Vexa-ai/vexa#1587). Its pin is the last block in this file, and it is the opposite assertion:
 *  the input field says nothing while a turn runs.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { joinInterim, streamChatTurn, type ChatStreamCallbacks } from "../chatStream";
import type { Op } from "../../workbench/agent-window";

function sseResponse(chunks: string[]): Response {
  const enc = new TextEncoder();
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(c) { if (i < chunks.length) c.enqueue(enc.encode(chunks[i++])); else c.close(); },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}
const ev = (o: Record<string, unknown>) => `data: ${JSON.stringify(o)}\n\n`;

/** The reducer the chat surface applies, in the order the stream delivers events. Mirrors
 *  chat.tsx: a new tool closes the previous step, the turn's end closes the last one. */
function runTurn(chunks: string[]) {
  const seen: { ops: Op[][]; text: string[]; artifactsAt: number[] } = { ops: [], text: [], artifactsAt: [] };
  let ops: Op[] = [];
  let text = "";
  let afterTool = false;
  let events = 0;

  const toolOp = (tool: string): Op => {
    const name = tool.replace(/^mcp__[^_]+(?:_[^_]+)*?__/, "");
    return { icon: "zap", label: name, status: "running" };
  };
  const settle = (l: Op[]) => l.map((o) => (o.status === "running" ? { ...o, status: "done" as const } : o));

  const cb: ChatStreamCallbacks = {
    onStarting: () => {},
    onStatus: () => {},
    onDelta: (t) => { events += 1; text = joinInterim(text, t, afterTool); afterTool = false; seen.text.push(text); },
    onTool: (tool) => {
      events += 1; afterTool = true;
      ops = [...settle(ops), toolOp(tool)];
      seen.ops.push(ops);
    },
    onArtifact: () => { seen.artifactsAt.push(events); },
    onCommit: () => {},
    onRejected: () => {},
    onModelFailure: () => {},
    onError: () => {},
    onProgress: () => {},
  };
  return streamChatTurn(
    { prompt: "p", session: "s", active: undefined },
    cb,
    { fetchImpl: (async () => sseResponse(chunks)) as unknown as typeof fetch, hardTimeoutMs: 2000,
      signal: new AbortController().signal },
  // NOTE: `renders` is the array of successive texts; `final` is the last one. Spreading a
  // `text` key over `seen.text` is how the first version of this test silently compared a
  // character count to a render count.
  ).then(() => ({ renders: seen.text, ops: seen.ops, artifactsAt: seen.artifactsAt, finalOps: settle(ops), final: text }));
}

describe("the step line while a turn runs", () => {
  it("every step is RUNNING as it arrives — never a tick mid-turn", async () => {
    const out = await runTurn([
      ev({ type: "tool-call", tool: "Read" }),
      ev({ type: "tool-call", tool: "mcp__vexa__entity_upsert" }),
      ev({ type: "tool-call", tool: "Bash" }),
      ev({ type: "turn-complete" }), ev({ type: "done", ok: true }),
    ]);
    // at each moment the LAST op — the one the step line renders — is running
    for (const snapshot of out.ops) {
      expect(snapshot[snapshot.length - 1].status).toBe("running");
    }
    // and the ones before it have settled, so the line reads as progress rather than a pile
    expect(out.ops[2].map((o) => o.status)).toEqual(["done", "done", "running"]);
  });

  it("the count ticks up, one per tool call", async () => {
    const out = await runTurn([
      ev({ type: "tool-call", tool: "Read" }),
      ev({ type: "tool-call", tool: "Edit" }),
      ev({ type: "tool-call", tool: "Bash" }),
      ev({ type: "done", ok: true }),
    ]);
    expect(out.ops.map((o) => o.length)).toEqual([1, 2, 3]);
  });

  it("the tick comes ONLY at the end, and then for every step", async () => {
    const out = await runTurn([
      ev({ type: "tool-call", tool: "Read" }),
      ev({ type: "tool-call", tool: "Bash" }),
      ev({ type: "turn-complete" }), ev({ type: "done", ok: true }),
    ]);
    expect(out.finalOps.every((o) => o.status === "done")).toBe(true);
    expect(out.finalOps).toHaveLength(2);
  });

  it("an MCP tool reads as its verb — `entity_upsert`, not `mcp__vexa__entity_upsert`", async () => {
    const out = await runTurn([ev({ type: "tool-call", tool: "mcp__vexa__entity_upsert" }), ev({ type: "done", ok: true })]);
    expect(out.finalOps[0].label).toBe("entity_upsert");
  });
});

describe("interim text streams as it arrives", () => {
  it("appears paragraph by paragraph, not in one block at the end (F40's separator)", async () => {
    const out = await runTurn([
      ev({ type: "message-delta", text: "First thought." }),
      ev({ type: "tool-call", tool: "Read" }),
      ev({ type: "message-delta", text: "Second thought." }),
      ev({ type: "done", ok: true }),
    ]);
    // the reader saw text BEFORE the turn ended — two renders, growing
    expect(out.renders.length).toBe(2);
    expect(out.renders[0]).toBe("First thought.");
    // and the tool call between them became a paragraph break, not a run-on
    expect(out.renders[1]).toBe("First thought.\n\nSecond thought.");
    expect(out.final).toBe("First thought.\n\nSecond thought.");
  });
});

describe("an artifact navigates DURING the turn", () => {
  it("fires as it arrives, not buffered until completion", async () => {
    const out = await runTurn([
      ev({ type: "message-delta", text: "a" }),
      ev({ type: "artifact", workspace: "acme", path: "README.md", focus: true }),
      ev({ type: "message-delta", text: "b" }),
      ev({ type: "message-delta", text: "c" }),
      ev({ type: "done", ok: true }),
    ]);
    // recorded after the 1st event and before the last two — i.e. mid-turn
    expect(out.artifactsAt).toEqual([1]);
  });
});

/** NO STATUS TEXT IN THE COMPOSER WHILE A TURN RUNS (Vexa-ai/vexa#1587).
 *
 *  Founder, 2026-09-06, on a screenshot of the input field reading
 *  `working · 1 step · james-spadafora.md` while the chat above already showed
 *  `Reading · james-spadafora.md · 1 step` and `Working…`:
 *
 *      "working · 2 steps · whats_waiting — remove that from the input field"
 *
 *  The composer cannot be mounted on its own — it is JSX inside `Chat`, which needs the whole
 *  service registry — so this pins the SOURCE, in the idiom of `errorPresentation.guard.test.ts`:
 *  the composer's own JSX may not contain a status string, and the transcript must still carry
 *  one. A grep guard is what stops the 46th site landing silently; it is what stops the second
 *  return of a line the founder has now asked to remove once.
 */
const SURFACES = join(dirname(fileURLToPath(import.meta.url)), "..");
const CHAT_TSX = readFileSync(join(SURFACES, "chat.tsx"), "utf8");
const AGENT_WINDOW_TSX = readFileSync(join(SURFACES, "..", "workbench", "agent-window.tsx"), "utf8");

/** The composer's JSX: `const composer = (` through the `);` that closes it (column 2 — every
 *  line inside is indented deeper). Everything the input field renders is in here. */
function composerJsx(src: string): string {
  const start = src.indexOf("const composer = (");
  expect(start, "chat.tsx no longer declares `const composer = (`").toBeGreaterThan(-1);
  const end = src.indexOf("\n  );", start);
  expect(end, "could not find the end of the composer JSX").toBeGreaterThan(start);
  return src.slice(start, end);
}

describe("the composer while a turn runs", () => {
  const composer = composerJsx(CHAT_TSX);

  it("renders no working/step/tool status string", () => {
    for (const forbidden of [/\bworking\b/i, /\bstep\b/i, /liveState/, /turnState/, /jobLine/, /data-live-state/]) {
      expect(composer, `the composer still renders ${forbidden}`).not.toMatch(forbidden);
    }
  });

  it("keeps the stop control — a handle is not narration", () => {
    expect(composer).toMatch(/aria-label="Stop"/);
    expect(composer).toMatch(/onClick=\{stop\}/);
  });

  it("the live-state span is gone from the surface entirely", () => {
    expect(CHAT_TSX).not.toMatch(/data-live-state/);
  });
});

describe("the chat's own step rows are the one place status is told", () => {
  it("the turn's status line and step count still render in the transcript", () => {
    expect(AGENT_WINDOW_TSX).toMatch(/data-turn-status/);
    expect(AGENT_WINDOW_TSX).toMatch(/\{n\} step/);
    // …and that count is the SERVER's once it sends one (Vexa-ai/vexa#1622), with this browser's
    // own tally as the fallback. What #1587 pins is WHERE a step count is told — here, in the
    // transcript, never in the composer — not which of the two numbers fills it.
    expect(AGENT_WINDOW_TSX).toMatch(/typeof t\.steps === "number" \? t\.steps : t\.ops\.length/);
  });

  it("a background job's line renders there too — it moved, it did not disappear", () => {
    expect(CHAT_TSX).toMatch(/data-job-line/);
    // and it is inside the conversation, not the composer
    expect(composerJsx(CHAT_TSX)).not.toMatch(/data-job-line/);
    expect(CHAT_TSX).toMatch(/<JobRows jobs=\{jobs\} \/>/);
  });
});

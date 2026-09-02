/** F66 — A RUNNING TURN MUST LOOK LIKE ONE.
 *
 *  Founder, watching an 18-step `entity_upsert` turn: *"i know it's working now, but it just stays
 *  like it's stale — need to update animations or work etc."*
 *
 *  The cause was one word. Every op was appended with `status: "done"`, so the step line rendered a
 *  green TICK from the first tool call and never moved again — an eighteen-step turn looked
 *  finished eighteen times. These pin the corrected reducer end to end against a scripted stream:
 *  what the ops look like WHILE it runs, and what they look like when it stops.
 */
import { describe, it, expect } from "vitest";
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

describe("the composer's state line", () => {
  // the exact string the founder will read beside the stop button
  const line = (busy: boolean, ops: Op[]) => {
    const step = ops.length ? ops[ops.length - 1] : null;
    const label = step ? (step.label.split(" · ").pop() ?? step.label) : "";
    return busy
      ? ["working", ops.length ? `${ops.length} step${ops.length === 1 ? "" : "s"}` : "", label].filter(Boolean).join(" · ")
      : "";
  };

  it("reads `working · 18 steps · entity_upsert`", () => {
    const ops = Array.from({ length: 18 }, (_, i) =>
      ({ icon: "zap", label: i === 17 ? "entity_upsert" : "Read · x.md", status: "done" }) as Op);
    expect(line(true, ops)).toBe("working · 18 steps · entity_upsert");
  });

  it("says `working` before any tool has run, and one STEP is singular", () => {
    expect(line(true, [])).toBe("working");
    expect(line(true, [{ icon: "zap", label: "Read · x.md", status: "running" }])).toBe("working · 1 step · x.md");
  });

  it("is CLEARED the moment the turn completes", () => {
    expect(line(false, [{ icon: "zap", label: "entity_upsert", status: "done" }])).toBe("");
  });
});

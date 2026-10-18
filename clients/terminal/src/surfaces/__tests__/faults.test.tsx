/** P18 in the chat — every typed fault renders as WHO failed · WHAT kind · the detail · the remedy.
 *
 *  The founder, 0.13.2 demo stack: he sent "hi", the runtime refused to start his agent, and the chat
 *  said "Internal Server Error" — *"this fails our 'fail loud' principle"*. The server now types the
 *  failure at its adapter (`source` + `kind`); these pin the half the person reads: one block per
 *  fault, for every kind either adapter can send, with nothing generic left standing in for it.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { FaultBlock } from "../../workbench/agent-window";
import { faultHeadline, faultLine, readFault, type Fault } from "../faults";
import { blockedLine, jobLine } from "../jobs";
import { blockSubmission, inboxRows, runnable, submitToInbox, SubmitRefused, flushOutbox, rememberUnsent } from "../inbox";
import { streamChatTurn, type ChatStreamCallbacks } from "../chatStream";

afterEach(() => { cleanup(); vi.restoreAllMocks(); try { localStorage.clear(); } catch { /* jsdom */ } });

/** Every kind the two server adapters can send (`shared/runtime_fault.KINDS`, `llm/faults.KINDS`)
 *  plus the chat proxy's own floor — with the headline each must render under. */
const KINDS: [Fault, string][] = [
  [{ source: "runtime", kind: "spawn_refused", status: 502, detail: "a previous agent for this chat is still registered", remedy: "Retry in a few seconds." },
   "Agent runtime · could not start your agent (502)"],
  [{ source: "runtime", kind: "quota_exceeded", status: 429, detail: "you already have as many agents running as you are allowed", remedy: "Retry when one has finished." },
   "Agent runtime · agent limit reached (429)"],
  [{ source: "runtime", kind: "unauthorized", status: 401, detail: "the runtime refused agent-api's credential", remedy: "Align RUNTIME_API_TOKEN." },
   "Agent runtime · credential refused (401)"],
  [{ source: "runtime", kind: "unreachable", status: null, detail: "the agent runtime could not be reached", remedy: "Retry in a moment." },
   "Agent runtime · unreachable"],
  [{ source: "runtime", kind: "unavailable", status: 503, detail: "the runtime answered with an error (503)", remedy: "Retry." },
   "Agent runtime · unavailable (503)"],
  [{ source: "runtime", kind: "refused", status: 400, detail: "the runtime refused the request (400)" },
   "Agent runtime · refused the request (400)"],
  [{ source: "runtime", kind: "not_found", status: 404, detail: "the runtime does not know this agent" },
   "Agent runtime · agent not found (404)"],
  [{ source: "runtime", kind: "bad_response", status: null, detail: "the runtime answered with something agent-api could not read" },
   "Agent runtime · answered unreadably"],
  [{ source: "model-provider", kind: "unpaid", status: 402, provider: "openrouter.ai", model: "anthropic/claude-sonnet-4",
     detail: "Insufficient credits. Add more using https://openrouter.ai/settings/credits",
     remedy: "Add credit at openrouter.ai, or choose another model under Settings → Models." },
   "Model provider · out of credits (402)"],
  [{ source: "model-provider", kind: "unauthorized", status: 401, provider: "openrouter.ai", detail: "User not found.", remedy: "Check the API key." },
   "Model provider · credential refused (401)"],
  [{ source: "model-provider", kind: "rate_limited", status: 429, provider: "api.anthropic.com", remedy: "Wait a moment and send it again." },
   "Model provider · rate limited (429)"],
  [{ source: "model-provider", kind: "unavailable", status: null, provider: "api.anthropic.com", detail: "ConnectTimeout" },
   "Model provider · unavailable"],
  [{ source: "model-provider", kind: "refused", status: 400, provider: "openrouter.ai", detail: "invalid model id" },
   "Model provider · refused the request (400)"],
  [{ source: "agent-api", kind: "internal", status: 500, detail: "the agent service failed while taking this message" },
   "Agent service · failed (500)"],
  [{ source: "gateway", kind: "unreachable", status: null, detail: "the terminal could not reach the Vexa gateway" },
   "Vexa gateway · unreachable"],
  [{ source: "vexa-tools", kind: "access_expired", status: 401,
     detail: "This turn ran past its tool access, which expired at 10:30:00 UTC; the Vexa tool calls it made after that were refused.",
     remedy: "Send it again: the next turn starts with fresh tool access." },
   "Vexa tools · tool access expired (401)"],
  [{ source: "agent-worker", kind: "tools_unconfined", status: null,
     detail: "This turn did not run: the worker could not hand desk/notes.md to the user the model's tools run as (Operation not permitted), and it never runs them as itself.",
     remedy: "Send it again. If it fails the same way, an operator must fix the ownership or permissions of that path in the workspace store." },
   "Your agent · could not confine the model's tools"],
];

describe("a typed fault renders who failed, what kind, the detail and the remedy", () => {
  it.each(KINDS)("$source · $kind", (fault, headline) => {
    const retry = vi.fn();
    render(<FaultBlock failed={{ fault, retry: true }} onRetry={retry} />);
    const block = screen.getByRole("alert");
    expect(block.getAttribute("data-fault-source")).toBe(fault.source);
    expect(block.getAttribute("data-fault-kind")).toBe(fault.kind);
    expect(block.querySelector("[data-fault-headline]")?.textContent).toBe(headline);
    if (fault.detail) expect(block.querySelector("[data-fault-detail]")?.textContent?.toLowerCase())
      .toContain(fault.detail.toLowerCase().slice(0, 30));
    if (fault.remedy) expect(block.querySelector("[data-fault-remedy]")?.textContent).toBe(fault.remedy);
    expect(block.textContent).not.toMatch(/Internal Server Error|Something went wrong|Model inference failed/);
    fireEvent.click(screen.getByText("Retry"));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("draws no Retry where there is nothing to send again", () => {
    render(<FaultBlock failed={{ fault: KINDS[0][0] }} onRetry={() => {}} />);
    expect(screen.queryByText("Retry")).toBeNull();
  });

  it("reads only a whole record — a half one falls back to the old message", () => {
    expect(readFault({ source: "runtime" })).toBeNull();
    expect(readFault({ kind: "unpaid" })).toBeNull();
    expect(readFault("Internal Server Error")).toBeNull();
    expect(readFault({ source: "runtime", kind: "spawn_refused", status: 502 })?.status).toBe(502);
  });

  it("spells the one-line form the rows use", () => {
    expect(faultHeadline(KINDS[8][0])).toBe("Model provider · out of credits (402)");
    expect(faultLine(KINDS[0][0])).toBe(
      "Agent runtime · could not start your agent (502) — a previous agent for this chat is still registered");
  });
});

// ── the stream reader routes a fault to the fault renderer, not to the generic text ─────────────

function sse(events: object[]): Response {
  const enc = new TextEncoder();
  const text = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
  let sent = false;
  const body = new ReadableStream<Uint8Array>({
    pull(c) { if (!sent) { sent = true; c.enqueue(enc.encode(text)); } else c.close(); },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}

function callbacks() {
  const seen = { faults: [] as Fault[], errors: [] as string[], modelFailures: 0 };
  const cb: ChatStreamCallbacks = {
    onStarting: () => {}, onDelta: () => {}, onTool: () => {}, onCommit: () => {}, onRejected: () => {},
    onModelFailure: () => { seen.modelFailures += 1; },
    onError: (m) => { seen.errors.push(m); },
    onFault: (f) => { seen.faults.push(f); },
  };
  return { seen, cb };
}

const run = (r: Response, cb: ChatStreamCallbacks) => streamChatTurn(
  { prompt: "hi", session: "s", active: undefined }, cb,
  { fetchImpl: (async () => r) as unknown as typeof fetch, signal: new AbortController().signal,
    now: () => 0, sleep: async () => {}, reconnectBackoffMs: 0 });

describe("streamChatTurn — typed faults end the turn as faults", () => {
  it("a spawn refusal folded into an `error` event is a fault, not 'Internal Server Error'", async () => {
    const { seen, cb } = callbacks();
    const res = await run(sse([{ type: "error", status: 502, message: "The agent runtime could not start your agent.",
                                 fault: KINDS[0][0] }]), cb);
    expect(res.terminal).toBe(true);
    expect(seen.faults.map((f) => f.kind)).toEqual(["spawn_refused"]);
    expect(seen.errors).toEqual([]);
  });

  it("a provider 402 on `done` is a fault, not 'Model inference failed'", async () => {
    const { seen, cb } = callbacks();
    await run(sse([{ type: "done", ok: false, reply: "The model provider (openrouter.ai) is out of credit (402).",
                     fault: KINDS[8][0] }, { type: "turn-complete" }]), cb);
    expect(seen.faults.map((f) => [f.source, f.kind])).toEqual([["model-provider", "unpaid"]]);
    expect(seen.modelFailures).toBe(0);
  });

  it("a non-ok answer that names its fault ends the turn on it instead of retrying for the hard cap", async () => {
    const { seen, cb } = callbacks();
    const r = { ok: false, status: 502, json: async () => ({ detail: "…", fault: KINDS[0][0] }) } as unknown as Response;
    const res = await run(r, cb);
    expect(res.terminal).toBe(true);
    expect(seen.faults).toHaveLength(1);
  });

  it("an `error` with no fault is still the plain message — a server one release behind", async () => {
    const { seen, cb } = callbacks();
    await run(sse([{ type: "error", message: "boom" }]), cb);
    expect(seen.errors).toEqual(["boom"]);
  });
});

// ── a queued row behind the failure says it is BLOCKED, and by what ──────────────────────────────

describe("a blocked row is not a queued one", () => {
  const spawn = KINDS[0][0];

  it("a server row the runtime fault blocks says so instead of 'queued behind the current turn'", () => {
    const rows = inboxRows([{ entry: "1-0", id: "q-1", kind: "", target: "", display: "the context message",
                              at: 1, blocked: spawn }]);
    expect(rows[0].blocked?.kind).toBe("spawn_refused");
    expect(jobLine(rows)).toBe(`queued · the context message · ${blockedLine(spawn)}`);
    expect(jobLine(rows)).not.toContain("queued behind the current turn");
  });

  it("nothing blocked is runnable, so nothing blocked is attached to", () => {
    const items = [{ entry: "1-0", id: "a", kind: "", target: "", display: "a", at: 1, blocked: spawn },
                   { entry: "2-0", id: "b", kind: "", target: "", display: "b", at: 1 }];
    expect(runnable(items).map((i) => i.id)).toEqual(["b"]);
  });

  it("a refused submission becomes ONE blocked row this browser keeps for its retry", () => {
    const once = blockSubmission([], { id: "s-1", display: "hi" }, spawn);
    const twice = blockSubmission(once, { id: "s-1", display: "hi" }, { ...spawn, kind: "unreachable" });
    expect(twice).toHaveLength(1);
    expect(twice[0]).toMatchObject({ id: "s-1", outbox: true, blocked: { kind: "unreachable" }, display: "hi" });
  });

  it("a refused submit carries the server's fault and keeps the unsent copy", async () => {
    const refused = (async () => ({ ok: false, status: 502, json: async () => ({ detail: "…", fault: spawn }) })) as unknown as typeof fetch;
    const err = await submitToInbox({ id: "s-1", session: "m", prompt: "hi" }, refused).catch((e) => e);
    expect(err).toBeInstanceOf(SubmitRefused);
    expect((err as SubmitRefused).fault?.kind).toBe("spawn_refused");
    expect(localStorage.getItem("vexa.outbox.m")).toContain("s-1");
  });

  it("an outbox flush the server refuses reports the fault rather than retrying in silence", async () => {
    rememberUnsent({ id: "s-1", session: "m", prompt: "hi", display: "hi" });
    const refused = (async () => ({ ok: false, status: 502, json: async () => ({ fault: spawn }) })) as unknown as typeof fetch;
    const seen: string[] = [];
    expect(await flushOutbox("m", refused, (s, f) => seen.push(`${s.id}:${f.kind}`))).toEqual([]);
    expect(seen).toEqual(["s-1:spawn_refused"]);
  });
});

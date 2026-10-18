/** A MOMENT'S UNAVAILABILITY IS NOT A DANGER (the founder, 2026-10-13: "UPDATE UI SO THAT WE DO NOT
 *  HAVE RED DANGEROUS THINGS HERE").
 *
 *  His chat: every send that met the gateway's one-second 503 ("Authentication temporarily
 *  unavailable, retry", Retry-After: 1) drew a red-bordered box naming "Agent service · unavailable
 *  (503)", and he sent the same message three times to get one answer. These pin the other shape:
 *  the chat retries that kind of fault itself, under the SAME turn id (agent-api runs a turn once
 *  per id), honouring Retry-After; when it cannot get through it says so in one muted line with
 *  the status behind a "details" toggle; and red stays for what is destructive or a security refusal.
 */
import { describe, it, expect, afterEach, vi, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, act } from "@testing-library/react";
import { FaultBlock } from "../agent-window";
import { isTransientFault, type Fault } from "../faults";
import { streamChatTurn, type ChatStreamCallbacks } from "../chatStream";

vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => undefined }) }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const AUTH_503: Fault = { source: "agent-api", kind: "unavailable", status: 503,
  detail: "Authentication temporarily unavailable, retry" };

function sse(events: object[], ids = false): Response {
  const enc = new TextEncoder();
  const text = events.map((e, i) => `${ids ? `id: ${i + 1}-0\n` : ""}data: ${JSON.stringify(e)}\n\n`).join("");
  let sent = false;
  const body = new ReadableStream<Uint8Array>({
    pull(c) { if (!sent) { sent = true; c.enqueue(enc.encode(text)); } else c.close(); },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}

/** What the terminal's /api/chat proxy answers when the gateway's auth hop is down for a second. */
const gateway503 = (retryAfter: number | undefined = 1) => sse([{
  type: "error", status: 503, message: "Authentication temporarily unavailable, retry",
  ...(retryAfter !== undefined ? { retry_after: retryAfter } : {}), fault: AUTH_503 }]);

const answered = () => sse([
  { type: "turn-accepted", turn_id: "w1" },
  { type: "message-delta", text: "hello" },
  { type: "done", ok: true },
  { type: "turn-complete", turn_id: "w1" },
], true);

function harness(responses: (() => Response)[]) {
  const seen = { faults: [] as Fault[], errors: [] as string[], deltas: [] as string[], statuses: [] as (string | null)[],
                 sleeps: [] as number[], bodies: [] as { turn_id: string }[] };
  const cb: ChatStreamCallbacks = {
    onStarting: () => {}, onDelta: (t) => { seen.deltas.push(t); }, onTool: () => {}, onCommit: () => {},
    onRejected: () => {}, onModelFailure: () => {},
    onError: (m) => { seen.errors.push(m); },
    onFault: (f) => { seen.faults.push(f); },
    onStatus: (p) => { seen.statuses.push(p); },
  };
  let i = 0;
  const fetchImpl = (async (_u: unknown, init?: RequestInit) => {
    seen.bodies.push(JSON.parse(String(init?.body ?? "{}")));
    const r = responses[Math.min(i, responses.length - 1)]();
    i += 1;
    return r;
  }) as unknown as typeof fetch;
  const run = () => streamChatTurn({ prompt: "hi", session: "s", active: undefined }, cb,
    { fetchImpl, signal: new AbortController().signal, now: () => 0,
      sleep: async (ms) => { seen.sleeps.push(ms); }, reconnectBackoffMs: 0 });
  return { seen, run };
}

describe("a transient fault before the turn starts is retried for the same message", () => {
  it("a 503 with Retry-After is retried after that long, and the answer arrives with no fault", async () => {
    const { seen, run } = harness([() => gateway503(1), answered]);
    const res = await run();
    expect(seen.faults).toEqual([]);
    expect(seen.errors).toEqual([]);
    expect(seen.deltas.join("")).toContain("hello");
    expect(seen.sleeps[0]).toBe(1000);
    expect(res.terminal).toBe(true);
  });

  it("retries under the SAME turn id, so agent-api runs the turn once", async () => {
    const { seen, run } = harness([() => gateway503(1), () => gateway503(1), answered]);
    await run();
    expect(seen.bodies).toHaveLength(3);
    expect(new Set(seen.bodies.map((b) => b.turn_id)).size).toBe(1);
  });

  it("keeps the turn in its working state while it waits — never clears it mid-retry", async () => {
    const { seen, run } = harness([() => gateway503(1), answered]);
    await run();
    const lastNull = seen.statuses.lastIndexOf(null);
    expect(seen.statuses.indexOf(null)).toBe(lastNull);          // null only once, at the very end
    expect(lastNull).toBe(seen.statuses.length - 1);
  });

  it("backs off 1s, 2s, 4s, 8s… honouring a longer Retry-After, within ~30s, then reports ONE fault", async () => {
    const { seen, run } = harness([() => gateway503(1)]);
    const res = await run();
    expect(seen.faults).toHaveLength(1);
    expect(isTransientFault(seen.faults[0])).toBe(true);
    expect(seen.sleeps.slice(0, 4)).toEqual([1000, 2000, 4000, 8000]);
    expect(seen.sleeps.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(30000);
    expect(res.terminal).toBe(true);
    expect(seen.statuses.at(-1)).toBeNull();
  });

  it("waits at least what Retry-After says", async () => {
    const { seen, run } = harness([() => gateway503(5), answered]);
    await run();
    expect(seen.sleeps[0]).toBe(5000);
  });

  it("an edge 429/503 that never reached the proxy is retried too", async () => {
    const edge = () => ({ ok: false, status: 429, headers: new Headers({ "Retry-After": "2" }),
                          json: async () => { throw new Error("html"); } }) as unknown as Response;
    const { seen, run } = harness([edge, answered]);
    await run();
    expect(seen.errors).toEqual([]);
    expect(seen.faults).toEqual([]);
    expect(seen.sleeps[0]).toBe(2000);
  });

  it("a fault that is not transient ends the turn at once", async () => {
    const unpaid = { source: "model-provider", kind: "unpaid", status: 402, detail: "out of credit" };
    const { seen, run } = harness([() => sse([{ type: "error", status: 402, message: "x", fault: unpaid }]), answered]);
    await run();
    expect(seen.faults.map((f) => f.kind)).toEqual(["unpaid"]);
    expect(seen.sleeps).toEqual([]);
  });

  it("a fault after the turn began is not retried — the turn may already have run", async () => {
    const { seen, run } = harness([() => sse([{ type: "turn-accepted", turn_id: "w1" },
      { type: "error", status: 503, message: "x", fault: AUTH_503 }], true), answered]);
    await run();
    expect(seen.faults).toHaveLength(1);
    expect(seen.bodies).toHaveLength(1);
  });
});

describe("the chat proxy passes Retry-After on", () => {
  it("a gateway 503 with Retry-After: 1 reaches the stream as retry_after 1", async () => {
    const { POST } = await import("../../app/api/chat/route");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "Authentication temporarily unavailable, retry" }),
      { status: 503, headers: { "Retry-After": "1", "Content-Type": "application/json" } })));
    const req = { text: async () => "{}", headers: new Headers(), signal: new AbortController().signal } as never;
    const text = await (await POST(req)).text();
    const ev = JSON.parse(text.split("\n").find((l) => l.startsWith("data: "))!.slice(6));
    expect(ev.retry_after).toBe(1);
    expect(ev.fault.kind).toBe("unavailable");
  });
});

describe("an exhausted transient fault is one calm line", () => {
  it("says it could not reach Vexa — no status, no service name, no red, details behind a toggle", () => {
    render(<FaultBlock failed={{ fault: AUTH_503, retry: true }} onRetry={() => {}} />);
    const block = screen.getByRole("alert");
    const summary = block.querySelector("[data-fault-summary]")?.textContent ?? "";
    expect(summary).toMatch(/Couldn.t reach Vexa just now/);
    expect(summary).not.toMatch(/503|Agent service|unavailable/);
    expect(block.outerHTML).not.toMatch(/danger/);
    expect(block.getAttribute("data-fault-tone")).toBe("neutral");
    const details = block.querySelector("details[data-fault-details]");
    expect(details).not.toBeNull();
    expect(details?.textContent).toContain("Agent service · unavailable (503)");
    expect(screen.getByText("Retry")).toBeTruthy();
  });

  it("every other fault is neutral too — red only for a security refusal", () => {
    const unpaid: Fault = { source: "model-provider", kind: "unpaid", status: 402, detail: "out of credit",
                            remedy: "Add credit." };
    const { unmount } = render(<FaultBlock failed={{ fault: unpaid }} />);
    expect(screen.getByRole("alert").outerHTML).not.toMatch(/danger/);
    expect(screen.getByRole("alert").querySelector("[data-fault-summary]")?.textContent).not.toMatch(/402/);
    unmount();
    render(<FaultBlock failed={{ fault: { source: "agent-worker", kind: "tools_unconfined", status: null } }} />);
    expect(screen.getByRole("alert").getAttribute("data-fault-tone")).toBe("danger");
  });
});

// ── the whole Chat: Retry after an exhausted fault leaves one bubble and no trace ─────────────────

const stream = vi.hoisted(() => ({
  calls: [] as { req: { prompt: string }; cb: Record<string, ((...a: never[]) => void) | undefined>; finish: () => void }[],
}));
vi.mock("../chatStream", async (importOriginal) => {
  const real = await importOriginal<typeof import("../chatStream")>();
  return {
    ...real,
    // the stream-level tests above call the real reader; the Chat below gets a controllable one
    streamChatTurn: (req: unknown, cb: unknown, opts: { fetchImpl?: unknown }) => opts?.fetchImpl
      ? real.streamChatTurn(req as never, cb as never, opts as never)
      : new Promise((resolve) => {
        stream.calls.push({ req: req as never, cb: cb as never,
          finish: () => resolve({ sawVisibleOutput: true, terminal: true, aborted: false, cursor: "42-0" }) });
      }),
  };
});

import { Chat } from "../chat";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService, ASK_CHAT_EVENT } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../platform/layout";

describe("Retry after the chat gave up", () => {
  beforeEach(() => {
    stream.calls.length = 0;
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ turns: [], sessions: [], pending: [] }) })) as never;
  });

  it("re-sends the same message under the same bubble, and success leaves no fault behind", async () => {
    const container = createContainer([
      reg(LayoutServiceId, () => createLayoutService("files")),
      reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
    ]);
    render(<ServicesProvider container={container}><Chat params={{ session: "calm-retry" }} /></ServicesProvider>);
    await act(async () => { window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: "summarise my day" } })); });
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await act(async () => { stream.calls[0].cb.onFault?.(AUTH_503 as never); stream.calls[0].finish(); });
    fireEvent.click(await screen.findByText("Retry"));
    await waitFor(() => expect(stream.calls.length).toBe(2));
    await act(async () => { stream.calls[1].cb.onDelta?.("Here it is." as never); stream.calls[1].finish(); });
    await screen.findByText("Here it is.");
    expect(screen.getAllByText("summarise my day")).toHaveLength(1);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.body.textContent).not.toMatch(/Couldn.t reach Vexa/);
  });
});

// ── a message submitted while a turn runs gets the same patience ──────────────────────────────────
import { submitToInbox, SubmitRefused } from "../inbox";

describe("a submission that meets a transient fault", () => {
  const ok = () => ({ ok: true, status: 200, json: async () => ({ ok: true, pending: [], cursor: "9-0" }) }) as unknown as Response;
  const busy = () => ({ ok: false, status: 503, headers: new Headers({ "Retry-After": "1" }),
                        json: async () => ({ detail: "Authentication temporarily unavailable, retry" }) }) as unknown as Response;
  const sub = { id: "s-1", session: "calm-inbox", prompt: "and this", display: "and this" } as never;

  it("is re-sent under its own id after Retry-After, and lands", async () => {
    const ids: string[] = [];
    const sleeps: number[] = [];
    let n = 0;
    const f = (async (_u: unknown, init?: RequestInit) => {
      ids.push(JSON.parse(String(init?.body)).turn_id); n += 1; return n === 1 ? busy() : ok();
    }) as unknown as typeof fetch;
    await submitToInbox(sub, f, async (ms) => { sleeps.push(ms); });
    expect(ids).toEqual(["s-1", "s-1"]);
    expect(sleeps).toEqual([1000]);
  });

  it("gives up after ~30s of waiting and is refused once", async () => {
    const sleeps: number[] = [];
    await expect(submitToInbox(sub, (async () => busy()) as unknown as typeof fetch,
      async (ms) => { sleeps.push(ms); })).rejects.toBeInstanceOf(SubmitRefused);
    expect(sleeps.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(30000);
    expect(sleeps.length).toBeGreaterThan(2);
  });
});

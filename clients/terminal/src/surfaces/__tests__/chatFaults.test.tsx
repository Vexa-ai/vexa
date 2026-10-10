/** THE FOUNDER'S "hi", AGAIN — through the whole Chat, with the failures typed (P18).
 *
 *  0.13.2 demo stack: he sent "hi", the runtime refused to start his agent, and the chat said
 *  "Internal Server Error" while his other message sat "queued behind the current turn" for a turn
 *  that was never coming — *"this fails our 'fail loud' principle"*. The next failure in line was
 *  the model provider refusing with 402, out of credit.
 *
 *  Mounted whole, like `actWhileBusy`, because the defect lived in the wiring: a stream event, a
 *  refused submit and a pending list each have to reach the person as the SAME sentence.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, act, fireEvent } from "@testing-library/react";

const stream = vi.hoisted(() => ({
  calls: [] as { req: { prompt: string }; opts: { attachFrom?: string };
                 cb: Record<string, ((...a: never[]) => void) | undefined>; finish: () => void }[],
}));

/** agent-api's inbox and its runtime, faked: `refuse` is the typed fault the next submit answers. */
const server = vi.hoisted(() => ({
  items: [] as Record<string, unknown>[],
  refuse: null as Record<string, unknown> | null,
}));

vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: (req: unknown, cb: unknown, opts: unknown) =>
    new Promise((resolve) => {
      stream.calls.push({ req: req as never, opts: (opts ?? {}) as never, cb: cb as never,
        finish: () => resolve({ sawVisibleOutput: true, terminal: true, aborted: false, cursor: "42-0" }) });
    }),
}));

import { Chat } from "../chat";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";
import { ASK_CHAT_EVENT } from "../../canvas/actions";

const SPAWN_REFUSED = {
  source: "runtime", kind: "spawn_refused", op: "spawn", status: 502,
  detail: "a previous agent for this chat is still registered",
  remedy: "Retry in a few seconds; if it keeps happening, an operator must remove the stale agent workload.",
};
const UNPAID = {
  source: "model-provider", kind: "unpaid", status: 402, provider: "openrouter.ai", model: "anthropic/claude-sonnet-4",
  detail: "Insufficient credits. Add more using https://openrouter.ai/settings/credits",
  remedy: "Add credit at openrouter.ai, or choose another model under Settings → Models.",
};

const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);

let seq = 0;
function mountChat() {
  seq += 1;
  render(<ServicesProvider container={container()}><Chat params={{ session: `fault-test-${seq}` }} /></ServicesProvider>);
}

async function say(prompt: string) {
  await act(async () => { window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt } })); });
}

const submits = () => (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
  .filter((c) => String(c[0]).startsWith("/api/chat/submit"))
  .map((c) => JSON.parse(String((c[1] as RequestInit).body)) as { prompt: string; turn_id: string });

beforeEach(() => {
  stream.calls.length = 0;
  server.items.length = 0;
  server.refuse = null;
  try { localStorage.clear(); } catch { /* jsdom */ }
  globalThis.fetch = vi.fn(async (url: unknown, init?: RequestInit) => {
    const u = String(url);
    if (u.startsWith("/api/chat/submit")) {
      if (server.refuse) {
        const fault = server.refuse;
        return { ok: false, status: 502, json: async () => ({ detail: "refused", fault }) };
      }
      const b = JSON.parse(String(init?.body ?? "{}")) as { prompt?: string; turn_id?: string };
      server.items = server.items.filter((i) => i.id !== b.turn_id);
      server.items.push({ entry: `${server.items.length + 1}-0`, id: b.turn_id ?? "", kind: "", target: "",
                          display: b.prompt ?? "", at: Date.now() / 1000 });
      return { ok: true, status: 200, json: async () => ({ ok: true, pending: [...server.items], cursor: "9-0" }) };
    }
    if (u.startsWith("/api/chat/pending")) {
      return { ok: true, status: 200, json: async () => ({ pending: [...server.items], cursor: "9-0" }) };
    }
    return { ok: true, status: 200, json: async () => ({ turns: [], sessions: [] }) };
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("a spawn the runtime refused", () => {
  it("shows WHO failed, what kind, the detail and the remedy — never 'Internal Server Error'", async () => {
    mountChat();
    await say("hi");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await act(async () => { stream.calls[0].cb.onFault?.(SPAWN_REFUSED as never); stream.calls[0].finish(); });

    const block = await screen.findByRole("alert");
    expect(block.getAttribute("data-fault-source")).toBe("runtime");
    expect(block.textContent).toContain("Agent runtime · could not start your agent (502)");
    expect(block.textContent).toContain("A previous agent for this chat is still registered.");
    expect(block.textContent).toContain("an operator must remove the stale agent workload");
    expect(document.body.textContent).not.toMatch(/Internal Server Error|Something went wrong/);
  });

  it("can be retried from the block — the same words, sent once", async () => {
    mountChat();
    await say("hi");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await act(async () => { stream.calls[0].cb.onFault?.(SPAWN_REFUSED as never); stream.calls[0].finish(); });
    fireEvent.click(await screen.findByText("Retry"));
    await waitFor(() => expect(stream.calls.length).toBe(2));
    expect(stream.calls[1].req.prompt).toContain("hi");
    expect(screen.queryByText("Retry")).toBeNull();          // one press, one retry
  });

  it("a message submitted behind it is BLOCKED by it, says so, and retries under its own id", async () => {
    mountChat();
    await say("hi");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    server.refuse = SPAWN_REFUSED;
    await say("and the context message");                    // mid-turn → the server's inbox
    const row = await screen.findByText(/blocked — Agent runtime · could not start your agent \(502\)/);
    expect(row.textContent).toContain("and the context message");
    expect(document.body.textContent).not.toContain("queued behind the current turn");

    server.refuse = null;                                     // the runtime is back
    fireEvent.click(screen.getAllByText("Retry").at(-1)!);
    await waitFor(() => expect(submits()).toHaveLength(2));
    expect(submits()[1].turn_id).toBe(submits()[0].turn_id);  // the same submission, not a second one
    await waitFor(() => expect(screen.queryByText(/blocked —/)).toBeNull());
  });

  it("a chat that loads with a blocked queue shows it blocked and does not attach to wait for nothing", async () => {
    server.items.push({ entry: "1-0", id: "q-1", kind: "", target: "", display: "the context message",
                        at: Date.now() / 1000, blocked: SPAWN_REFUSED });
    mountChat();
    const row = await screen.findByText(/blocked — Agent runtime/);
    expect(row.closest("[data-job-line]")?.getAttribute("data-job-blocked")).toBe("spawn_refused");
    expect(screen.getByText("Retry")).toBeTruthy();
    await new Promise((r) => setTimeout(r, 50));
    expect(stream.calls).toHaveLength(0);                     // nothing will run, so nothing is watched
  });
});

describe("a model provider out of credit", () => {
  it("ends the turn with the provider, the kind, its own words and where to pay", async () => {
    mountChat();
    await say("hi");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await act(async () => { stream.calls[0].cb.onFault?.(UNPAID as never, "out of credit" as never); stream.calls[0].finish(); });
    const block = await screen.findByRole("alert");
    expect(block.getAttribute("data-fault-kind")).toBe("unpaid");
    expect(block.textContent).toContain("Model provider (openrouter.ai) · out of credit (402)");
    expect(block.textContent).toContain("Insufficient credits");
    expect(block.textContent).toContain("Add credit at openrouter.ai");
    expect(document.body.textContent).not.toContain("Model inference failed");
  });
});

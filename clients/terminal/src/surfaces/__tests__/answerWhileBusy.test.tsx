/** A MESSAGE SENT WHILE THE CHAT IS STILL ANSWERING IS ALWAYS ANSWERED — ON SCREEN, UNDER IT.
 *
 *  The founder, 2026-10-10, on the dogfood stack: *"sometimes chat does not answer if asked while
 *  it's not yet answered."* His message showed; the worker answered it; the chat never showed the
 *  answer. Three ways that happened, one test each:
 *
 *  1. The worker takes a mid-turn message the instant the turn in front ends, so by the time the
 *     chat — idle again — asked "is anything queued?", nothing was. It watched nothing.
 *  2. The turn in front ended while the submission was still in flight: the chat looked, found
 *     nothing, and nothing made it look again once the server had the message.
 *  3. The answer streamed on the SAME view as the turn in front (the worker writes a turn's
 *     write-back after its `done`, and takes the next message meanwhile), and was folded into that
 *     turn's bubble — above the person's own message, where it read as no answer at all.
 *
 *  The whole `Chat` is mounted, as in `actWhileBusy.test.tsx`: each failure lived in the wiring.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, waitFor, act, fireEvent } from "@testing-library/react";

const stream = vi.hoisted(() => ({
  calls: [] as {
    req: { prompt: string };
    opts: { attachFrom?: string };
    cb: Record<string, ((...a: never[]) => unknown) | undefined>;
    finish: () => void;
  }[],
}));

/** agent-api, faked: the inbox, plus how many turns the worker has started past a cursor. */
const server = vi.hoisted(() => ({
  items: [] as { entry: string; id: string; kind: string; target: string; display: string; at: number }[],
  taken: 0,
  holdSubmit: null as null | Promise<void>,
  pendingCalls: [] as string[],
}));

vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: (req: unknown, cb: unknown, opts: unknown) =>
    new Promise((resolve) => {
      stream.calls.push({
        req: req as never, opts: (opts ?? {}) as never, cb: cb as never,
        finish: () => resolve({ sawVisibleOutput: true, terminal: true, aborted: false, cursor: "42-0" }),
      });
    }),
}));

import { Chat } from "../chat";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService, ASK_CHAT_EVENT } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";

const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);

let seq = 0;
function mountChat() {
  seq += 1;
  render(
    <ServicesProvider container={container()}>
      <Chat params={{ session: `answer-busy-${seq}` }} />
    </ServicesProvider>,
  );
}

/** Type into the composer and press Enter — what the founder did. Mid-turn this paints the person's
 *  own bubble at once and submits the words to the server's inbox. */
async function type(text: string) {
  const input = document.querySelector("textarea") as HTMLTextAreaElement;
  fireEvent.change(input, { target: { value: text } });
  await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
}

async function ask(prompt: string) {
  await act(async () => { window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt } })); });
}

beforeEach(() => {
  stream.calls.length = 0;
  server.items.length = 0;
  server.taken = 0;
  server.holdSubmit = null;
  server.pendingCalls.length = 0;
  try { localStorage.clear(); } catch { /* jsdom always has one */ }
  globalThis.fetch = vi.fn(async (url: unknown, init?: RequestInit) => {
    const u = String(url);
    if (u.startsWith("/api/chat/submit")) {
      if (server.holdSubmit) await server.holdSubmit;
      const b = JSON.parse(String(init?.body ?? "{}")) as { prompt?: string; turn_id?: string };
      server.items.push({ entry: `${server.items.length + 1}-0`, id: b.turn_id ?? "", kind: "", target: "",
                          display: b.prompt ?? "", at: Date.now() / 1000 });
      return { ok: true, status: 200, json: async () => ({ ok: true, pending: [...server.items], cursor: "9-0" }) };
    }
    if (u.startsWith("/api/chat/pending")) {
      server.pendingCalls.push(u);
      const after = new URL(u, "http://x").searchParams.get("after");
      return { ok: true, status: 200, json: async () => ({
        pending: [...server.items], cursor: "50-0", ...(after ? { taken: server.taken } : {}) }) };
    }
    return { ok: true, status: 200, json: async () => ({ turns: [], sessions: [] }) };
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("a message sent while the chat is still answering", () => {
  it("is watched even when the worker took it before the chat looked", async () => {
    mountChat();
    await ask("what is on today?");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await ask("and this as well");
    await waitFor(() => expect(server.items).toHaveLength(1));

    // The turn in front ends, and in the same instant the worker takes the message: it is no
    // longer QUEUED, it is RUNNING. Only `taken` says so.
    server.items.length = 0;
    server.taken = 1;
    await act(async () => { stream.calls[0].finish(); });

    await waitFor(() => expect(stream.calls.length).toBe(2));
    expect(stream.calls[1].req.prompt).toBe("");               // an attach — never a second send
    expect(stream.calls[1].opts.attachFrom).toBe("42-0");      // from exactly where the chat read to
    expect(server.pendingCalls.some((u) => u.includes("after=42-0"))).toBe(true);
  });

  it("is watched when the turn in front ended while the submission was still in flight", async () => {
    mountChat();
    await ask("what is on today?");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    let release = () => {};
    server.holdSubmit = new Promise<void>((r) => { release = r; });
    await ask("and this as well");

    // The view closes first; the chat looks, and there is nothing yet — the POST has not landed.
    await act(async () => { stream.calls[0].finish(); });
    await waitFor(() => expect(server.pendingCalls.length).toBeGreaterThan(0));
    expect(stream.calls.length).toBe(1);

    // Now the server has it, and the worker takes it at once.
    server.taken = 1;
    await act(async () => { release(); });
    await waitFor(() => expect(stream.calls.length).toBe(2));
    expect(stream.calls[1].opts.attachFrom).toBe("42-0");
  });

  it("is answered in its own bubble, below the message, when it streams on the same view", async () => {
    mountChat();
    await ask("what is on today?");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    const cb = stream.calls[0].cb;
    await act(async () => {
      cb.onTurn?.("t1" as never, false as never);
      cb.onDelta?.("A-answer" as never);
    });
    await type("and this as well");
    await waitFor(() => expect(server.items).toHaveLength(1));
    server.items.length = 0;      // the worker took it
    await act(async () => {
      expect(cb.onTurn?.("t2" as never, true as never)).toBe(true);
      cb.onDelta?.("B-answer" as never);
      cb.onTurn?.("t1" as never, false as never);    // A's write-back trailing in
      cb.onDelta?.("" as never);
      stream.calls[0].finish();
    });

    const text = document.body.textContent ?? "";
    expect(text.indexOf("A-answer")).toBeGreaterThan(-1);
    expect(text.indexOf("A-answer")).toBeLessThan(text.indexOf("and this as well"));
    expect(text.indexOf("and this as well")).toBeLessThan(text.indexOf("B-answer"));
  });
});

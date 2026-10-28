/** STOP ENDS THE TURN ON THE SERVER, NOT ONLY IN THIS TAB (app.dev, 2026-10-15).
 *
 *  A turn's tool — a fourteen-minute grep — kept running after the person pressed Stop: Stop aborted
 *  this browser's fetch and nothing else, so the worker went on with the turn. The next message got a
 *  409 and the chat drew "Session conflict. Retry — session-store · session conflict": the store's
 *  own vocabulary, and a Retry that could not work while the old turn still ran.
 *
 *  Now Stop also asks agent-api to stop the turn (`POST /api/chat/stop`), and the conflict renders as
 *  one calm line with the control that resolves it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, act, fireEvent } from "@testing-library/react";

const stream = vi.hoisted(() => ({
  calls: [] as { req: { prompt: string; session: string }; opts: { signal?: AbortSignal };
                 cb: Record<string, ((...a: never[]) => void) | undefined>; finish: () => void }[],
}));

vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: (req: unknown, cb: unknown, opts: unknown) =>
    new Promise((resolve, reject) => {
      const o = (opts ?? {}) as { signal?: AbortSignal };
      o.signal?.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
      stream.calls.push({ req: req as never, opts: o as never, cb: cb as never,
        finish: () => resolve({ sawVisibleOutput: true, terminal: true, aborted: false, cursor: "42-0" }) });
    }),
}));

import { Chat } from "../chat";
import { stopTurn } from "../inbox";
import { faultHeadline, faultLine, faultSummary } from "../faults";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../platform/layout";
import { ASK_CHAT_EVENT } from "../../platform";

/** The 409's typed fault, as agent-api answers it and the terminal's proxy forwards it. */
const conflict = (session: string) => ({
  source: "session-store", kind: "session_conflict", session, action: "stop",
  remedy: "Stop it, then send your message again.",
});

const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);

let seq = 0;
function mountChat(): string {
  seq += 1;
  const session = `stop-test-${seq}`;
  render(<ServicesProvider container={container()}><Chat params={{ session }} /></ServicesProvider>);
  return session;
}

async function say(prompt: string) {
  await act(async () => { window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt } })); });
}

const stops = () => (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
  .filter((c) => String(c[0]) === "/api/chat/stop")
  .map((c) => ({ init: c[1] as RequestInit, body: JSON.parse(String((c[1] as RequestInit).body)) as { session: string } }));

beforeEach(() => {
  stream.calls.length = 0;
  try { localStorage.clear(); } catch { /* jsdom */ }
  globalThis.fetch = vi.fn(async (url: unknown) => {
    const u = String(url);
    if (u === "/api/chat/stop") return { ok: true, status: 200, json: async () => ({ ok: true, stopped: 1 }) };
    if (u.startsWith("/api/chat/pending")) return { ok: true, status: 200, json: async () => ({ pending: [], cursor: "" }) };
    return { ok: true, status: 200, json: async () => ({ turns: [], sessions: [] }) };
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("stopTurn", () => {
  it("posts the chat's session to /api/chat/stop", async () => {
    const f = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ ok: true, stopped: 1 }) }) as unknown as Response);
    expect(await stopTurn("pchat-1", f as unknown as typeof fetch)).toBe(true);
    expect(f).toHaveBeenCalledWith("/api/chat/stop", expect.objectContaining({ method: "POST",
      body: JSON.stringify({ session: "pchat-1" }) }));
  });
  it("is false, and does not throw, when the stop fails", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const refused = vi.fn(async () => ({ ok: false, status: 502, json: async () => ({}) }) as unknown as Response);
    expect(await stopTurn("pchat-1", refused as unknown as typeof fetch)).toBe(false);
    const down = vi.fn(async () => { throw new TypeError("network"); });
    expect(await stopTurn("pchat-1", down as unknown as typeof fetch)).toBe(false);
    expect(warn).toHaveBeenCalled();
  });
});

describe("pressing Stop while a turn runs", () => {
  it("asks the server to stop this chat's turn, as well as dropping the connection", async () => {
    const session = mountChat();
    await say("grep the whole tree");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(stops()).toHaveLength(1));
    expect(stops()[0].init.method).toBe("POST");
    expect(stops()[0].body).toEqual({ session });
    expect(stream.calls[0].opts.signal?.aborted).toBe(true);
  });
});

describe("a 409: a previous reply is still running", () => {
  it("says so calmly, offers Stop it — never the store's own vocabulary", async () => {
    const session = mountChat();
    await say("hi");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await act(async () => { stream.calls[0].cb.onFault?.(conflict(session) as never); stream.calls[0].finish(); });

    expect((await screen.findAllByText(/A previous reply is still running/)).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Stop it" })).toBeTruthy();
    const body = document.body.textContent ?? "";
    expect(body).not.toMatch(/session conflict/i);
    expect(body).not.toContain("session-store");
    expect(body).not.toContain("session_conflict");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();   // a Retry cannot work yet
  });

  it("Stop it stops that session's turn, then offers to send the message again", async () => {
    const session = mountChat();
    await say("hi");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await act(async () => { stream.calls[0].cb.onFault?.(conflict(session) as never); stream.calls[0].finish(); });

    fireEvent.click(await screen.findByRole("button", { name: "Stop it" }));
    await waitFor(() => expect(stops()).toHaveLength(1));
    expect(stops()[0].body).toEqual({ session });
    expect(await screen.findByText(/Stopping the previous reply/)).toBeTruthy();

    fireEvent.click(await screen.findByRole("button", { name: "Send again" }));
    await waitFor(() => expect(stream.calls.length).toBe(2));
    expect(stream.calls[1].req.prompt).toContain("hi");
    expect(screen.queryByRole("button", { name: "Send again" })).toBeNull();
  });
});

describe("the words a session conflict renders in", () => {
  it("never spells the store's vocabulary, wherever it is drawn", () => {
    const f = { source: "session-store", kind: "session_conflict" };
    for (const s of [faultHeadline(f), faultLine(f), faultSummary(f)]) {
      expect(s).not.toMatch(/session[ _-]conflict|session-store/i);
    }
    expect(faultSummary(f)).toBe("A previous reply is still running.");
  });
});

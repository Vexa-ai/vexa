/** A QUEUED MESSAGE IS THE PERSON'S PENDING BUBBLE, and Send now sends (#1783; founder: Send now
 *  "is NOT CLICKABLE (does nothing)"). The whole Chat is mounted; the server is a fetch fake that
 *  holds one queued message until it is sent now or taken back. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: () => Promise.resolve({ sawVisibleOutput: true, terminal: true, aborted: false, cursor: "1-0" }),
}));

import { Chat } from "../chat";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../platform/layout";

const TEXT = "also check the renewal date and draft the reply";
let held = true;
let calls: { url: string; body?: string }[] = [];

beforeEach(() => {
  held = true; calls = [];
  globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), body: init?.body as string | undefined });
    const u = String(url);
    if (u.startsWith("/api/chat/pending/now")) { held = false; return { ok: true, status: 200, json: async () => ({ ok: true }) }; }
    if (u.startsWith("/api/chat/pending/withdraw")) { const w = held ? 1 : 0; held = false; return { ok: true, status: 200, json: async () => ({ withdrawn: w }) }; }
    if (u.startsWith("/api/chat/pending")) {
      return { ok: true, status: 200, json: async () => ({ busy: true, pending: held ? [{ entry: "1-1", id: "q-1", display: TEXT }] : [] }) };
    }
    return { ok: true, status: 200, json: async () => ({ turns: [], sessions: [] }) };
  }) as unknown as typeof fetch;
  try { localStorage.clear(); } catch { /* jsdom */ }
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

let seq = 0;
const mount = () => render(
  <ServicesProvider container={createContainer([
    reg(LayoutServiceId, () => createLayoutService("files")),
    reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
  ])}>
    <Chat params={{ session: `queued-${++seq}` }} />
  </ServicesProvider>);

describe("a queued message", () => {
  it("is a pending bubble with its whole text, a Queued label and three pills — no mono status line", async () => {
    mount();
    expect(await screen.findByText(TEXT)).toBeTruthy();
    expect(screen.getByText("Queued · after the current reply")).toBeTruthy();
    for (const name of ["Send now", "Edit", "Remove"]) expect(screen.getByRole("button", { name })).toBeTruthy();
    expect(document.querySelector("[data-job-line]")).toBeNull();
  });

  it("Send now sends: the server is asked to run it now", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Send now" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/chat/pending/now")).toBe(true));
    const sent = calls.find((c) => c.url === "/api/chat/pending/now")!;
    expect(JSON.parse(sent.body!)).toMatchObject({ id: "q-1" });
  });

  it("Edit takes it back into the box; Remove takes it back", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await waitFor(() => expect((screen.getByPlaceholderText(/Type \/ for skills/) as HTMLTextAreaElement).value).toBe(TEXT));
    expect(calls.some((c) => c.url === "/api/chat/pending/withdraw")).toBe(true);
  });
});

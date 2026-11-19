/** THE HOME OPENS AT THE TOP (0.13.4 S0, mobile validation G1: "every load scrolls the home 1,017px
 *  down", so a phone opens on Files instead of the greeting, the composer and the Now strip).
 *
 *  The home is the empty conversation's `emptyExtra`, drawn inside the chat's scroller. The chat's
 *  "follow the newest" rule (stickToBottom) ran on every content change — including the one that
 *  draws the home — and pinned the box to its bottom. An empty conversation has nothing newest to
 *  follow: it is read from its top. Mounted whole, because the defect lives in the wiring.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";

vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: () => new Promise(() => {}),
}));

import { Chat } from "../chat";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../platform/layout";

const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);

const saved: Record<string, PropertyDescriptor | undefined> = {};
let turns: unknown[] = [];

beforeEach(() => {
  turns = [];
  try { localStorage.clear(); } catch { /* jsdom */ }
  // a home taller than the screen: 1,100px of content in a 100px box
  for (const k of ["scrollHeight", "clientHeight"]) saved[k] = Object.getOwnPropertyDescriptor(Element.prototype, k);
  Object.defineProperty(Element.prototype, "scrollHeight", { configurable: true, get() { return 1100; } });
  Object.defineProperty(Element.prototype, "clientHeight", { configurable: true, get() { return 100; } });
  globalThis.fetch = vi.fn(async (u: unknown) =>
    new Response(JSON.stringify(String(u).includes("/history") ? { turns } : { turns: [], sessions: [], pending: [], meetings: [] }), { status: 200, headers: { "content-type": "application/json" } }),
  ) as unknown as typeof fetch;
});
afterEach(() => {
  cleanup();
  for (const [k, d] of Object.entries(saved)) { if (d) Object.defineProperty(Element.prototype, k, d); else delete (Element.prototype as unknown as Record<string, unknown>)[k]; }
});

let seq = 0;
const scroller = (el: HTMLElement) => {
  let n: HTMLElement | null = el;
  while (n && n.style.overflowY !== "auto") n = n.parentElement;
  return n!;
};

describe("the home inside an empty chat", () => {
  it("opens at the top: the chat's follow-the-newest does not pin it to the bottom", async () => {
    seq += 1;
    render(<ServicesProvider container={container()}><Chat params={{ session: `home-top-${seq}` }} emptyExtra={<div data-testid="home">home</div>} /></ServicesProvider>);
    const home = await screen.findByTestId("home");
    const box = scroller(home);
    // let every effect the load set off run (loading → false, pending refresh)
    await new Promise((r) => setTimeout(r, 50));
    expect(box.scrollTop).toBe(0);
  });

  it("a conversation with turns still opens on the newest", async () => {
    seq += 1;
    turns = [{ role: "user", text: "hello", user_text: "hello" }, { role: "agent", text: "Hi there, newest reply.", ops: [] }];
    render(<ServicesProvider container={container()}><Chat params={{ session: `home-top-${seq}` }} emptyExtra={<div data-testid="home">home</div>} /></ServicesProvider>);
    const box = scroller(await screen.findByText("Hi there, newest reply."));
    await waitFor(() => expect(box.scrollTop).toBe(1100));
    expect(screen.queryByTestId("home")).toBeNull();
  });
});

/** "Make a card" on a chat's "…" in the rail (#1783, desktop ADR D2; UI decisions 15c/15d): offered
 *  only while `desktop_cards` is on and only for a row with a chat; it makes a CRM card titled as the
 *  chat, about `chat:<id>`. */
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, it, expect, vi } from "vitest";
vi.mock("../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../surfaces/chatActivity", () => ({ useChatActive: () => false }));
vi.mock("../../ui-kit/primitives/Toast", () => ({ toast: () => {}, Toaster: () => null }));
import { setFeatureOverride } from "../../platform/features";
import { resetDesktopStore } from "../desktop/desktopApi";
import { Rail } from "../Rail";

let calls: { url: string; body: unknown }[] = [];
beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url === "/api/desktop") return new Response(JSON.stringify({ version: 1, undo_version: null, sections: [], proposals: [], filters: [], saved: [], hidden_workspaces: 0, presence_visibility: "off", cards_on: true }), { status: 200 });
    return new Response(JSON.stringify({ status: "saved", card: { id: "r7" } }), { status: 200 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); resetDesktopStore(); setFeatureOverride("all"); });

const row = { key: "c:1", chatId: "pchat-1", meetingId: null, label: "Weekly sync", whenLabel: "Sat", workspaces: [] } as any;
const rail = () => render(<Rail rows={[row]} selKey={null} onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} />);

it("makes a card about the chat, titled as the chat", async () => {
  rail();
  fireEvent.click(screen.getByRole("button", { name: "Actions for Weekly sync" }));
  fireEvent.click(screen.getByRole("menuitem", { name: /Make a card/ }));
  await waitFor(() => expect(calls.filter((c) => c.url === "/api/desktop/card/create")).toHaveLength(1));
  expect(calls.find((c) => c.url === "/api/desktop/card/create")!.body).toEqual({ title: "Weekly sync", about: "chat:pchat-1" });
});

it("is not offered with desktop_cards off", () => {
  setFeatureOverride({ desktop_cards: false });
  rail();
  fireEvent.click(screen.getByRole("button", { name: "Actions for Weekly sync" }));
  expect(screen.queryByRole("menuitem", { name: /Make a card/ })).toBeNull();
  expect(calls.filter((c) => c.url.startsWith("/api/desktop/card/"))).toHaveLength(0);
});

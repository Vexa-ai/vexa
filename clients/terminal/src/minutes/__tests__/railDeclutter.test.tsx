/** A CALM RAIL (founder, 2026-10-14, live call: "total mess here"). His row for the shared routine
 *  chat read "ait · ⏱ routine · shared · live" with no name left — the text chips ate the title. A
 *  row is now its name, ONE icon when it is a call, a voice note, shared or a routine, and its time;
 *  the sections read Pinned · Routines · (your folders) · Recent · Hidden. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, within } from "@testing-library/react";
import { Rail } from "../Rail";
import { chatsFromSessions, railRows } from "../chats";
import { applyFolders, type RailFolders } from "../railFolders";
vi.mock("../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../surfaces/chatActivity", () => ({ useChatActive: () => false }));
afterEach(cleanup);

const F: RailFolders = { served: true, order: {}, filings: { "c:pin": "pinned" }, folders: [
  { id: "pinned", name: "Pinned", builtin: true, collapsed: false },
  { id: "routines", name: "Routines", builtin: true, collapsed: false },
  { id: "ungrouped", name: "Ungrouped", builtin: true, collapsed: false },
  { id: "hidden", name: "Hidden", builtin: true, collapsed: true },
] };
const chats = chatsFromSessions([
  { session: "ait", title: "AI Tinkerers Lisbon", workspaces: ["personal", "ait"], target: "ait", shared: true, participants: 2, role: "owner",
    routine: { flow: "bot-ait", scope: "user:1", version: 1 }, meeting: "9121" },
  { session: "pin", title: "AIT", workspaces: ["personal", "ait"], target: "ait" },
  { session: "plain", title: "Chat 12:30" },
]);
const meetings = [
  { id: "9121", title: "AIT planning call", platform: "Google Meet", native_id: "x", status: "live", live_status: "active", start_time: new Date().toISOString() },
  { id: "9050", title: "Call 9:52", platform: "vexa_call", native_id: "c", status: "past", live_status: "completed", start_time: new Date(Date.now() - 3600e3).toISOString() },
  { id: "8990", title: "Voice note", platform: "voice_note", native_id: "v", status: "past", live_status: "completed", start_time: new Date(Date.now() - 7200e3).toISOString() },
] as never[];

describe("rail rows", () => {
  const rows = applyFolders(railRows(chats, meetings), F);
  const draw = () => render(<Rail rows={rows} folders={F} selKey={null} onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} onMoveRows={() => {}} />);

  it("carry no text chips — the name is always there", () => {
    const { container } = draw();
    expect(container.querySelectorAll(".vx-row2-main .vx-tag")).toHaveLength(0);
    const ait = container.querySelector('[data-row-key="c:ait"]')!;
    expect(ait.querySelector(".vx-row2-title")!.textContent).toBe("AI Tinkerers Lisbon");
    expect(ait.textContent).not.toMatch(/routine|shared|\bait\b/);
  });
  it("carry at most one icon: call, voice note, shared or routine", () => {
    const { container } = draw();
    const icon = (k: string) => Array.from(container.querySelectorAll(`[data-row-key="${k}"] [data-row-icon]`)).map((e) => e.getAttribute("data-row-icon"));
    expect(icon("c:ait")).toEqual(["shared"]);
    expect(icon("m:9050")).toEqual(["call"]);
    expect(icon("m:8990")).toEqual(["voice"]);
    expect(icon("c:plain")).toEqual([]);
    expect(icon("c:pin")).toEqual([]);                    // a pinned row is in Pinned: no pin glyph, no workspace tag
  });
  it("sections read Pinned · Routines · Recent · Hidden", () => {
    draw();
    expect(screen.getByRole("region", { name: "Recent" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Ungrouped" })).toBeNull();
    expect(within(screen.getByRole("region", { name: "Routines" })).getByText("AI Tinkerers Lisbon")).toBeTruthy();
  });
});

describe("the row knows what kind of meeting it is", () => {
  it("a call and a voice note", () => {
    const rows = railRows([], meetings);
    expect(rows.find((r) => r.key === "m:9050")!.kind).toBe("call");
    expect(rows.find((r) => r.key === "m:8990")!.kind).toBe("voice");
  });
});

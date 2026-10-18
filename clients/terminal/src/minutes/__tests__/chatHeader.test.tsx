/** ONE CALM CHAT HEADER (founder, 2026-10-14, on a live call: "total mess here"). Title, "Live", a
 *  scope dropdown, +, the meeting chip "…et… live", "Stop bot" and "Transcript" all fought for one
 *  line and the title was overlapped. Now: the title (truncates, never overlapped) · the profile chip
 *  · at most ONE live-meeting chip — "● Live · <meeting>" — whose click opens the meeting view in the
 *  right panel, where Stop bot and the transcript live. */
import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { LiveMeetingChip, meetingToOpen } from "../ChatMeetingStrip";
import { ContextBar } from "../ContextBar";
import { Participants } from "../Participants";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const live = { id: "118", title: "Google Meet · ojr-rviw-pqz", platform: "Google Meet", native_id: "ojr-rviw-pqz", status: "live", live_status: "active" } as never;
const waiting = { ...(live as object), live_status: "awaiting_admission" } as never;
const done = { id: "119", title: "Retro", platform: "zoom", native_id: "1", status: "completed" } as never;

describe("the live-meeting chip", () => {
  it("one button: ● Live · <meeting>; a click opens the meeting view — no Stop bot, no Transcript", () => {
    const onOpen = vi.fn();
    const { container } = render(<LiveMeetingChip meeting={live} onOpen={onOpen} />);
    const buttons = container.querySelectorAll("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].textContent).toBe("Live · Google Meet · ojr-rviw-pqz");
    expect(screen.queryByRole("button", { name: /Stop bot/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Transcript/ })).toBeNull();
    fireEvent.click(buttons[0]);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
  it("says where the bot is before it is live", () => {
    const { container } = render(<LiveMeetingChip meeting={waiting} onOpen={() => {}} />);
    expect(container.textContent).toBe("Waiting to be let in · Google Meet · ojr-rviw-pqz");
  });
  it("is gone once the meeting ended", () => {
    const { container } = render(<LiveMeetingChip meeting={done} onOpen={() => {}} />);
    expect(container.innerHTML).toBe("");
  });
});

describe("a live meeting opens its view in the right panel, once", () => {
  it("opens a happening meeting the chat has not opened yet, and never twice", () => {
    expect(meetingToOpen("chat-1", live, new Set())).toBe("chat-1:118");
    expect(meetingToOpen("chat-1", live, new Set(["chat-1:118"]))).toBeNull();   // closed by the reader: stays closed
    expect(meetingToOpen("chat-1", done, new Set())).toBeNull();
    expect(meetingToOpen("chat-1", undefined, new Set())).toBeNull();
  });
  it("the shell wires it: the chip in the header, the auto-open effect, no strip with its own Stop", () => {
    const src = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");
    expect(src).toContain("<LiveMeetingChip");
    expect(src).not.toContain("<ChatMeetingStrip");
    expect(src).toContain("meetingToOpen(");
    expect(src).not.toContain("onStop={() => { void stopBot(chatMeeting)");
  });
});

describe("the header", () => {
  const sel = { kind: "chat", chatId: "chat-1", label: "Weekly ops review — prep and follow-ups", workspaces: ["personal", "ops"], target: "ops" } as never;
  const scope = { workspaces: [], hidden: 0, line: "Work · writes to Ops · reads 3",
    chat: { session: "chat-1", target: "ops", reads: ["ops"], own_reads: true, profile: "work" }, defaults: { target: "", active: [] } };
  it("is the title, the profile chip and the live chip — no breadcrumb word, no status dot, no +", async () => {
    vi.stubGlobal("fetch", vi.fn(async (u: string) => new Response(JSON.stringify(
      String(u).includes("/api/workspace/states") ? scope : String(u).includes("/participants")
        ? { participants: [{ principal: "me", kind: "human", role: "owner" }], role: "owner", you: "me" } : {}))));
    const { container } = render(<ContextBar sel={sel} flavor="chat" memberships={[]} onAddWorkspace={() => {}} onRemoveWorkspace={() => {}}
      scope={{ session: "chat-1" }} trailing={<LiveMeetingChip meeting={live} onOpen={() => {}} />} />);
    await waitFor(() => expect(container.querySelector("[data-scope-text]")?.textContent).toBe("Work · writes to Ops · reads 3"));
    const head = container.querySelector('[data-pane="header"]')!;
    expect(head.textContent).not.toMatch(/Chat ›/);
    expect(head.querySelector(".bg-success")).toBeNull();
    expect(head.querySelector('[aria-label="Add a workspace to this chat"]')).toBeNull();
    expect(head.querySelectorAll("[data-live-meeting]")).toHaveLength(1);
  });
  it("a chat with a live meeting says Live once — on the chip, never a second badge", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(scope))));
    const msel = { ...(sel as object), kind: "meeting", meetingId: "118" } as never;
    const { container } = render(<ContextBar sel={msel} flavor="meeting · live" memberships={[]} onAddWorkspace={() => {}} onRemoveWorkspace={() => {}}
      scope={{ session: "chat-1" }} trailing={<LiveMeetingChip meeting={live} onOpen={() => {}} />} />);
    expect(container.querySelector("[data-flavor-badge]")).toBeNull();
    expect(container.textContent!.match(/Live/g)).toHaveLength(1);
  });
  it("a new chat has no title — never the previous chat's (founder, UI lab 2026-10-14)", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
    const draft = { kind: "chat", chatId: "draft-1", label: "Weekly ops review — prep and follow-ups", workspaces: ["personal"] } as never;
    const { container } = render(<ContextBar sel={draft} untitled flavor="chat" memberships={[]} onAddWorkspace={() => {}} onRemoveWorkspace={() => {}} />);
    expect(container.querySelector(".vx-ctx-title")?.textContent).toBe("");
  });
  it("the title keeps a floor and truncates; chips give way first", () => {
    const CSS = readFileSync(join(__dirname, "..", "minutes.css"), "utf8");
    const rule = (sel: string) => CSS.match(new RegExp(`${sel.replace(/[.]/g, "\\.")}\\s*\\{([^}]*)\\}`))?.[1] ?? "";
    expect(rule(".vx-ctx-title")).toMatch(/min-width:\s*\d+(\.\d+)?(em|rem|px)/);
    expect(rule(".vx-ctx-title")).toMatch(/flex:\s*1\s+1/);
    expect(rule(".vx-live-chip")).toMatch(/min-width:\s*0/);
    expect(rule(".vx-live-chip-text")).toMatch(/text-overflow:\s*ellipsis/);
    expect(rule(".vx-scope-cluster")).toMatch(/min-width:\s*0/);
  });
});

describe("people in the chat", () => {
  it("say nothing while it is just you; show who is in it once it is shared", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(
      { participants: [{ principal: "me", kind: "human", role: "owner" }], role: "owner", you: "me" }))));
    const solo = render(<Participants session="chat-1" />);
    await new Promise((r) => setTimeout(r, 20));
    expect(solo.container.querySelector("button")).toBeNull();
    cleanup();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(
      { participants: [{ principal: "me", kind: "human", role: "owner" }, { principal: "u2", kind: "human", role: "member" }],
        role: "owner", you: "me", names: { u2: "Ana" } }))));
    const shared = render(<Participants session="chat-1" />);
    await waitFor(() => expect(shared.container.querySelector("button")?.textContent).toBe("You, Ana"));
  });
});

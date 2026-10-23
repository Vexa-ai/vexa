/** A WORKSPACE TILE'S CALLS (#1783, UI decision 14): the last and live call count every meeting kind
 *  — Meet / Teams / Zoom / Jitsi bot meetings and Vexa calls — and the tile shows the PLATFORM ICON
 *  (the meeting header's icon, named for screen readers) before "Call now" / "Last call". Join opens
 *  a live bot meeting itself and a Vexa call's chat. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WorkspaceTile } from "../desktop/DesktopTiles";
import { OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../platform";
import type { WorkspaceItem } from "../../platform/desktop";

vi.mock("../desktop/desktopApi", () => ({ arrange: vi.fn(), knownSubject: () => "u1" }));

const drag = { onDragStart: () => {}, onDragOver: () => {}, onDrop: () => {} };
const tile = (over: Partial<WorkspaceItem> = {}): WorkspaceItem => ({
  kind: "workspace", ref: "workspace:alpha", workspace_kind: "shared", slug: "alpha", id: "alpha",
  name: "Alpha", access: "owner", write: true, members: [], last_activity: null, last_call: null, live_call: null,
  ...over,
});

function heard(name: string) {
  const got: unknown[] = [];
  const on = (e: Event) => got.push((e as CustomEvent).detail);
  window.addEventListener(name, on);
  return { got, off: () => window.removeEventListener(name, on) };
}

afterEach(cleanup);

describe("WorkspaceTile calls", () => {
  it("shows a last bot meeting with its platform icon before the line", () => {
    const { container } = render(<WorkspaceTile drag={drag} w={tile({
      last_call: { meeting: "501", platform: "zoom", line: "Pricing review · Oct 14", at: 1 } })} />);
    const icon = container.querySelector("[data-call-platform]")!;
    expect(icon.getAttribute("data-call-platform")).toBe("zoom");
    expect(icon.getAttribute("aria-label")).toBe("Zoom");
    const btn = screen.getByRole("button", { name: /Last call · Pricing review/ });
    expect(icon.compareDocumentPosition(btn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const open = heard(OPEN_MEETING_EVENT);
    fireEvent.click(btn);
    open.off();
    expect(open.got).toEqual([{ ref: "501" }]);
  });

  it("a live bot meeting: icon, its name, and Join opens the meeting", () => {
    const { container } = render(<WorkspaceTile drag={drag} w={tile({
      live_call: { meeting: "503", platform: "google_meet", line: "Standup", since: 1 } })} />);
    expect(container.querySelector("[data-call-platform]")!.getAttribute("aria-label")).toBe("Google Meet");
    expect(container.querySelector("[data-live-call]")!.textContent).toContain("Call now · Standup");
    expect(container.querySelector("[data-live-call]")!.textContent).not.toMatch(/people|person/);
    const open = heard(OPEN_MEETING_EVENT);
    const chat = heard(OPEN_SESSION_EVENT);
    fireEvent.click(screen.getByRole("button", { name: "Join" }));
    open.off(); chat.off();
    expect(open.got).toEqual([{ ref: "503" }]);
    expect(chat.got).toEqual([]);
  });

  it("a live Vexa call: its icon is named a Vexa call and Join opens its chat", () => {
    const { container } = render(<WorkspaceTile drag={drag} w={tile({
      live_call: { session: "pchat-x", meeting: "41", platform: "vexa_call", line: "Weekly sync", people: 2, since: 1 } })} />);
    expect(container.querySelector("[data-call-platform]")!.getAttribute("aria-label")).toBe("Vexa call");
    expect(container.querySelector("[data-live-call]")!.textContent).toContain("2 people");
    const chat = heard(OPEN_SESSION_EVENT);
    fireEvent.click(screen.getByRole("button", { name: "Join" }));
    chat.off();
    expect(chat.got).toEqual([{ session: "pchat-x" }]);
  });

  it("an answer without a platform (an older server) still draws the call, without an icon", () => {
    const { container } = render(<WorkspaceTile drag={drag} w={tile({
      last_call: { meeting: "7", line: "Weekly sync · Oct 14", at: 1 } })} />);
    expect(container.querySelector("[data-call-platform]")).toBeNull();
    expect(screen.getByRole("button", { name: /Last call · Weekly sync/ })).toBeTruthy();
  });
});

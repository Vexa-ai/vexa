/** "Use as meeting page" (#1783): a page bound to a meeting by identity, from the page's "…". */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const rows = [
  { id: "150", title: "", title_custom: "Vexa Channel", native_id: "abc", platform: "teams", start_time: "2026-10-15T17:00:00Z", participants: [] },
  { id: "149", title: "", native_id: "jmk-qxpo-yzg", platform: "google_meet", start_time: "2026-10-15T09:00:00Z", participants: [{ name: "Tatiana Sukhova" }] },
];
vi.mock("../../surfaces/liveMeetings", () => ({ useLiveMeetings: () => rows, refreshMeetings: vi.fn() }));

import { AttachMeetingDialog, attachMeetingPage } from "../AttachMeetingDialog";

const PATH = "kg/entities/meeting/vexa-channel-call-15-oct-2026.md";
let calls: { url: string; body: Record<string, unknown> }[] = [];
let answer: { status: number; body: unknown } = { status: 200, body: { path: PATH, workspace: "oenb" } };

beforeEach(() => {
  calls = [];
  answer = { status: 200, body: { path: PATH, workspace: "oenb" } };
  vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), body: JSON.parse(String(init?.body ?? "{}")) });
    return new Response(JSON.stringify(answer.body), { status: answer.status, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Use as meeting page", () => {
  it("lists meetings by their display name and binds the page to the one picked", async () => {
    const onClose = vi.fn(); const onAttached = vi.fn();
    render(<AttachMeetingDialog path={PATH} workspace="oenb" onClose={onClose} onAttached={onAttached} />);
    expect(screen.getByText("Vexa Channel")).toBeTruthy();
    expect(screen.getByText("Call with Tatiana Sukhova")).toBeTruthy();       // never an id
    expect(document.body.textContent).not.toContain("jmk-qxpo-yzg");
    fireEvent.click(document.querySelector('[data-attach-meeting-row="150"] input') as HTMLElement);
    fireEvent.click(document.querySelector("[data-attach-meeting-go]") as HTMLElement);
    await waitFor(() => expect(onAttached).toHaveBeenCalledWith("150"));
    expect(calls[0]).toEqual({ url: "/api/meeting/note/attach", body: { path: PATH, workspace: "oenb", meeting_id: "150" } });
    expect(onClose).toHaveBeenCalled();
  });

  it("a meeting that already has a page says which, and replaces only when asked", async () => {
    answer = { status: 409, body: { detail: { refused: "this meeting already has a page", current: { path: "kg/entities/meeting/x.md", workspace: "" }, said: "Meeting 150 already has its page at kg/entities/meeting/x.md." } } };
    render(<AttachMeetingDialog path={PATH} workspace="oenb" onClose={vi.fn()} />);
    fireEvent.click(document.querySelector('[data-attach-meeting-row="150"] input') as HTMLElement);
    fireEvent.click(document.querySelector("[data-attach-meeting-go]") as HTMLElement);
    await waitFor(() => expect(document.querySelector("[data-attach-meeting-error]")?.textContent).toContain("already has its page"));
    answer = { status: 200, body: { path: PATH, workspace: "oenb" } };
    fireEvent.click(document.querySelector("[data-attach-meeting-replace]") as HTMLElement);
    await waitFor(() => expect(calls.length).toBe(2));
    expect(calls[1].body).toMatchObject({ meeting_id: "150", replace: true });
  });

  it("the call itself: a refusal is a message, never a throw", async () => {
    answer = { status: 403, body: { detail: "not authorized for this meeting" } };
    const a = await attachMeetingPage(PATH, "", "999");
    expect(a.ok).toBe(false);
  });
});

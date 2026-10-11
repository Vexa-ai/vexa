/** RENAME A CALL — the write (`../callRename`) and the in-place editor (`../CallName`).
 *
 *  Founder, 2026-10-11: *"call rename needed, probably same as for chats"*.
 *
 *  The title is the meeting's own `data.title`, written through meeting-api's annotate route by row
 *  id. Only the owner may: the route answers 404 to anyone else, and the terminal shows a shared
 *  call's name as plain text with no control. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import type { MeetingMock } from "../../surfaces/meetingModel";

const store = vi.hoisted(() => ({ applied: [] as [string, string][], refreshed: 0 }));
vi.mock("../../surfaces/liveMeetings", () => ({
  applyMeetingTitle: (id: string, t: string) => { store.applied.push([id, t]); },
  refreshMeetings: () => { store.refreshed += 1; },
}));

import { canRenameCall, renameCall } from "../callRename";
import { CallName } from "../CallName";

const mtg = (o: Partial<MeetingMock> & { id: string }): MeetingMock => ({
  title: "x", when: "", status: "past", live_status: "completed", platform: "zoom",
  participants: [], mentioned: [], actions: [], transcript: [], insights: [], ...o,
});
const ok = () => ({ ok: true, status: 200, json: async () => ({}) }) as Response;

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  store.applied = []; store.refreshed = 0;
  fetchMock = vi.fn(async () => ok());
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("renameCall — one front door, no copy", () => {
  it("writes the meeting's title through the annotate route by row id, then updates the store", async () => {
    await renameCall("42", "  Campbell intro ");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/meetings/42/annotate");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ title: "Campbell intro" });
    expect(store.applied).toEqual([["42", "Campbell intro"]]);
    expect(store.refreshed).toBe(1);
  });

  it("an empty name is the route's clear — sent as an empty title", async () => {
    await renameCall("42", "   ");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ title: "" });
    expect(store.applied).toEqual([["42", ""]]);
  });

  it("DENY: a refusal (another person's meeting is a 404) throws and changes nothing locally", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 404, json: async () => ({}) } as Response);
    await expect(renameCall("999", "mine now")).rejects.toThrow(/not yours/);
    expect(store.applied).toEqual([]);
    expect(store.refreshed).toBe(0);
  });

  it("only the owner is offered a rename", () => {
    expect(canRenameCall(mtg({ id: "1" }))).toBe(true);
    expect(canRenameCall(mtg({ id: "1", shared: true }))).toBe(false);
    expect(canRenameCall(undefined)).toBe(false);
  });
});

describe("CallName — the header's title, edited in place like a chat's", () => {
  it("click → input with the name selected; Enter saves", async () => {
    render(<CallName meeting={mtg({ id: "7", native_id: "88410021706" })} label="Zoom · 88410021706" />);
    fireEvent.click(screen.getByRole("button", { name: "Rename Zoom · 88410021706" }));
    const input = screen.getByRole("textbox", { name: "Call name" }) as HTMLInputElement;
    expect(input.value).toBe("Zoom · 88410021706");
    expect(input.placeholder).toBe("Zoom · 88410021706");
    fireEvent.change(input, { target: { value: "Campbell" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(store.applied).toEqual([["7", "Campbell"]]));
    await waitFor(() => expect(screen.queryByRole("textbox")).toBeNull());
  });

  it("Escape cancels and writes nothing", () => {
    render(<CallName meeting={mtg({ id: "7", title_custom: "Campbell" })} label="Campbell" />);
    fireEvent.click(screen.getByRole("button", { name: "Rename Campbell" }));
    const input = screen.getByRole("textbox", { name: "Call name" });
    fireEvent.change(input, { target: { value: "Something else" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("an emptied name on a titled call reverts it to the default label", async () => {
    render(<CallName meeting={mtg({ id: "7", native_id: "88410021706", title_custom: "Campbell" })} label="Campbell" />);
    fireEvent.click(screen.getByRole("button", { name: "Rename Campbell" }));
    const input = screen.getByRole("textbox", { name: "Call name" }) as HTMLInputElement;
    expect(input.placeholder).toBe("Zoom · 88410021706");
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(store.applied).toEqual([["7", ""]]));
  });

  it("a refused save keeps the input and says why", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 502, json: async () => ({}) } as Response);
    render(<CallName meeting={mtg({ id: "7" })} label="Zoom · #7" />);
    fireEvent.click(screen.getByRole("button", { name: "Rename Zoom · #7" }));
    const input = screen.getByRole("textbox", { name: "Call name" });
    fireEvent.change(input, { target: { value: "Campbell" } });
    fireEvent.submit(input.closest("form")!);
    expect((await screen.findByRole("alert")).textContent).toMatch(/Couldn.t save/);
    expect(screen.getByRole("textbox", { name: "Call name" })).toBeTruthy();
  });

  it("DENY: a call shared with you shows its name with no rename control", () => {
    const { container } = render(<CallName meeting={mtg({ id: "7", shared: true, title_custom: "Board prep" })} label="Board prep" />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(container.textContent).toBe("Board prep");
  });
});

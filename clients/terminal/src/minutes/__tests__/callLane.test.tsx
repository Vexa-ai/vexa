/** R1 2.3 — a room call's lane. A guest is anyone on the call who is not a room member; with a guest
 *  Vexa proposes rather than writes. The lane lists what Vexa proposed or updated since the call
 *  started (D1's one proposal object), and a proposal is decided through its owner's path. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { changeItems, guestsOn, laneItems } from "../rooms/laneData";
import { CallLane } from "../rooms/CallLane";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const START = Date.parse("2026-10-16T10:00:00Z");

describe("guestsOn", () => {
  it("is anyone on the call who is not a room member — never the listener — and nobody until members are known", () => {
    const onCall = [{ id: "7", name: "Ines" }, { id: "guest-1", name: "Jonas" }, { id: "vexa-listener-call-1", name: "listener" }];
    expect(guestsOn(onCall, ["7", "2"]).map((g) => g.name)).toEqual(["Jonas"]);
    expect(guestsOn(onCall, null)).toEqual([]);
  });
});

describe("laneItems / changeItems", () => {
  it("keeps Vexa's OPEN proposals from the call's start, newest first; drops people's, decided and older ones", () => {
    const list = [
      { id: "a", status: "open", created_at: "2026-10-16T10:05:00Z", reason: "the venue said 120", target: { label: "Agenda" }, acts: ["accept", "decline"] },
      { id: "b", status: "accepted", created_at: "2026-10-16T10:01:00Z", target: { path: "pages/pilot-scope.md" } },
      { id: "d", status: "open", created_at: "2026-10-16T09:50:00Z" },
      { id: "e", status: "open", created_at: "2026-10-16T10:08:00Z", proposer: { kind: "person" } },
      { id: "f", status: "open", created_at: "2026-10-16T10:09:00Z", target: { path: "pages/pilot-scope.md" } },
    ];
    expect(laneItems(list, START).map((i) => [i.id, i.label])).toEqual([["f", "pilot-scope"], ["a", "Agenda"]]);
  });
  it("the call's own changes are 'updated' lines, undoable until undone", () => {
    const out = changeItems([
      { id: "cc-1", workspace: "w", path: "pages/agenda.md", at: START / 1000 + 60, undoable: true, undone_at: null },
      { id: "cc-2", workspace: "w", path: "pages/new.md", at: START / 1000 + 120, created: true, undoable: false },
      { id: "cc-3", workspace: "w", path: "pages/x.md", at: START / 1000 + 30, undoable: false, undone_at: START / 1000 + 90 },
    ]);
    expect(out.map((i) => [i.id, i.label, i.undoable, i.undone])).toEqual([["cc-2", "new", false, false], ["cc-1", "agenda", true, false], ["cc-3", "x", false, true]]);
  });
});

describe("CallLane", () => {
  it("members only: says Vexa updates the workspace; Transcript opens the meeting", () => {
    const open = vi.fn();
    render(<CallLane room="meet-1" guests={[]} items={[]} onTranscript={open} />);
    expect(document.querySelector("[data-call-lane]")!.getAttribute("data-call-lane")).toBe("members");
    expect(screen.getByText(/Vexa updates the workspace as you talk/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Transcript/ }));
    expect(open).toHaveBeenCalled();
  });
  it("with a guest: names them and says Vexa proposes; Accept goes to /api/changes/{id}/accept", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }) as unknown as Response);
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const decided = vi.fn();
    render(<CallLane room="meet-1" guests={[{ id: "g", name: "Jonas" }]} onDecided={decided}
      items={[{ id: "p1", kind: "proposed", label: "Agenda", reason: "because the venue said 120", at: "2026-10-16T10:05:00Z", acts: ["accept", "decline"] }]} />);
    expect(screen.getByText(/is a guest: Vexa proposes changes for members to accept/)).toBeTruthy();
    expect(screen.getByText("because the venue said 120")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(decided).toHaveBeenCalled());
    expect((fetchMock.mock.calls.at(-1) as unknown as [string])[0]).toBe("/api/changes/p1/accept");
  });
  it("Undo posts to the call's change and, refused, says the server's own words", async () => {
    const fetchMock = vi.fn(async () => ({ ok: false, status: 409, json: async () => ({ detail: { refused: "changed_since", said: "The page changed after Vexa's edit, so it was not undone. A proposal to restore it was made instead." } }) }) as unknown as Response);
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<CallLane room="meet-1" guests={[]} items={[{ id: "cc-1", kind: "updated", label: "agenda", reason: "", at: "2026-10-16T10:05:00Z", acts: [], undoable: true }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect((await screen.findByRole("alert")).textContent).toContain("A proposal to restore it was made instead");
    expect((fetchMock.mock.calls.at(-1) as unknown as [string])[0]).toBe("/api/calls/meet-1/changes/cc-1/undo");
  });
});

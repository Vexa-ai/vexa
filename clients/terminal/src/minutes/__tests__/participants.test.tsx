/** ADR-0048 — who is in a chat: listed read-only, removed by the owner, left by anyone else; people
 *  are added by @-mention in the composer, never here. While it is just you the header shows nothing
 *  at all (founder, 2026-10-14: declutter the header) — no "Just you", and no note about a store that
 *  is not configured, because there is nobody to list. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ApiError } from "../../data/apiClient";

const api = vi.hoisted(() => ({
  listParticipants: vi.fn(), addParticipant: vi.fn(), removeParticipant: vi.fn(),
}));
vi.mock("../../surfaces/sessionsApi", () => api);
import { Participants } from "../Participants";

const owner = { session_id: "s-1", you: "alice", role: "owner",
  participants: [{ principal: "alice", kind: "human", role: "owner" }] };

beforeEach(() => { Object.values(api).forEach((f) => f.mockReset()); });
afterEach(cleanup);

describe("Participants", () => {
  it("lists who is in the chat and lets the owner remove someone — and adds nobody", async () => {
    api.listParticipants.mockResolvedValue({ ...owner, participants: [...owner.participants,
      { principal: "bob", kind: "human", role: "member" }] });
    render(<Participants session="s-1" />);
    fireEvent.click(await screen.findByRole("button", { name: /People in this chat/ }));
    await waitFor(() => expect(screen.getByText("bob")).toBeTruthy());
    expect(screen.getByText("You")).toBeTruthy();
    // ADR-0048: people are added by @-mention in the composer; the header has no add form
    expect(screen.queryByLabelText("Add by email")).toBeNull();
    expect(screen.queryByPlaceholderText(/@/)).toBeNull();
    expect(screen.queryByText("Add")).toBeNull();
    expect(screen.queryByText(/not your workspaces/)).toBeNull();
    api.removeParticipant.mockResolvedValue({ removed: true, participants: owner.participants });
    fireEvent.click(screen.getByRole("button", { name: "Remove bob" }));
    await waitFor(() => expect(api.removeParticipant).toHaveBeenCalledWith("s-1", "bob"));
    expect(api.addParticipant).not.toHaveBeenCalled();
  });

  it("a thread the store has not seen is just you — and just you shows nothing", async () => {
    api.listParticipants.mockRejectedValue(new ApiError(404, "x", "/api/sessions/pchat-1/participants"));
    const { container } = render(<Participants session="pchat-1" />);
    await waitFor(() => expect(api.listParticipants).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 10));
    expect(container.innerHTML).toBe("");
  });

  it("a member can leave but cannot remove others", async () => {
    api.listParticipants.mockResolvedValue({ session_id: "s-1", you: "bob", role: "member",
      participants: [{ principal: "alice", kind: "human", role: "owner" }, { principal: "bob", kind: "human", role: "member" }] });
    render(<Participants session="s-1" count={2} />);
    expect(await screen.findByRole("button", { name: /People in this chat: 2/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /People in this chat/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Leave this chat" })).toBeTruthy());
    expect(screen.queryByLabelText("Add by email")).toBeNull();
  });

  it("a store that is not configured leaves the header empty — there is nobody to list", async () => {
    api.listParticipants.mockRejectedValue(new ApiError(503, "x", "/api/sessions/a/participants"));
    const { container } = render(<Participants session="a" />);
    await waitFor(() => expect(api.listParticipants).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 10));
    expect(container.innerHTML).toBe("");
  });

  it("the badge names who is in a shared chat as soon as the chat opens (#1783)", async () => {
    api.listParticipants.mockResolvedValue({ session_id: "s-1", you: "58", role: "member",
      participants: [{ principal: "58", kind: "human", role: "member" }, { principal: "59", kind: "human", role: "owner" }],
      names: { "58": "Collab a", "59": "Collab b" } });
    render(<Participants session="s-1" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /People in this chat/ }).textContent).toBe("You, Collab b"));
  });
});

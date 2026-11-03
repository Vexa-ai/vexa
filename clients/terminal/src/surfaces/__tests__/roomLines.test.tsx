/** A shared chat is live both ways (#1783, collaboration rehearsal 2026-10-14): a message one person
 *  posted without addressing the agent was stored and shown to nobody. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { RoomLines } from "../chat";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

const reply = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

describe("the lines in a shared chat", () => {
  it("shows each line with who wrote it", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(reply({
      you: "58", version: 3, names: { "59": "Collab b" },
      messages: [{ seq: 1, author: "59", text: "hi A, this is B", created: 1 },
                 { seq: 2, author: "58", text: "hi B", created: 2 }],
    }));
    render(<RoomLines session="s-1" />);
    expect(await screen.findByText(/hi A, this is B/)).toBeTruthy();
    expect(screen.getByText("Collab b")).toBeTruthy();
    expect(screen.getByText("You")).toBeTruthy();
  });

  it("reads again by itself and re-reads the conversation when another person's turn lands", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const f = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(reply({ you: "58", version: 3, names: {}, messages: [] }))
      .mockResolvedValue(reply({ you: "58", version: 4, names: { "59": "Collab b" },
        messages: [{ seq: 1, author: "59", text: "new line", created: 5 }] }));
    const onTurn = vi.fn();
    render(<RoomLines session="s-1" onTurn={onTurn} />);
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(await screen.findByText(/new line/)).toBeTruthy();
    expect(onTurn).toHaveBeenCalledTimes(1);
  });

  it("keeps reading a chat the store did not hold yet, so the owner of a chat shared later sees its lines", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const f = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "session not found" }), { status: 404, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValue(reply({ you: "58", version: 2, names: { "59": "Collab b" },
        messages: [{ seq: 1, author: "59", text: "I can book the venue", created: 5 }] }));
    render(<RoomLines session="pchat-1" />);
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(15100); });
    expect(await screen.findByText(/I can book the venue/)).toBeTruthy();
  });
});


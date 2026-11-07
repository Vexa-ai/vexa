/** A shared chat is ONE conversation (founder 2026-10-14, UI lab iteration 1): what people type to
 *  each other and the agent's turns are one stream in time order, each with its name — not a strip
 *  of lines above the composer. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { ChatConversation, interleave, composerPlaceholder, type RoomView } from "../chat";
import type { Turn } from "../agent-window";

afterEach(() => cleanup());

const turns: Turn[] = [
  { id: "u1", role: "user", text: "send a bot at 16:00", at: 100 },
  { id: "a1", role: "agent", text: "Done.", ops: [], at: 101 },
  { id: "u2", role: "user", text: "timing?", at: 300 },
  { id: "a2", role: "agent", text: "36 minutes.", ops: [], at: 301 },
];
const room: RoomView = { you: "171", names: { "171": "Dmitry", "203": "Valeriia" }, lines: [
  { seq: 1, author: "203", text: "hey", created: 150 },
  { seq: 2, author: "171", text: "so what about the date?", created: 200 },
  { seq: 3, author: "203", text: "before Friday", created: 400 },
] };

describe("a shared chat is one conversation", () => {
  it("puts each line between the turns by time", () => {
    const order = interleave(turns, room.lines).map((x) => (x.kind === "turn" ? x.turn.id : `l${x.line.seq}`));
    expect(order).toEqual(["u1", "a1", "l1", "l2", "u2", "a2", "l3"]);
  });

  it("a turn the history gave no time takes its neighbour's, and lines stay after an undated conversation", () => {
    const undated: Turn[] = [{ id: "u1", role: "user", text: "a" }, { id: "a1", role: "agent", text: "b", ops: [] }];
    expect(interleave(undated, room.lines).map((x) => x.kind)).toEqual(["turn", "turn", "line", "line", "line"]);
    const live: Turn[] = [...turns, { id: "u3", role: "user", text: "now", at: 500 }, { id: "a3", role: "agent", text: "…", ops: [] }];
    expect(interleave(live, room.lines).map((x) => (x.kind === "turn" ? x.turn.id : `l${x.line.seq}`)).slice(-3)).toEqual(["l3", "u3", "a3"]);
  });

  it("renders the lines in the stream with who wrote them", () => {
    const { container } = render(<ChatConversation turns={turns} room={room} />);
    const who = [...container.querySelectorAll("[data-turn-who]")].map((e) => e.textContent);
    expect(who).toEqual(["You", "Vexa", "Valeriia", "You", "You", "Vexa", "Valeriia"]);
    expect(container.querySelectorAll('[data-turn="line"]')).toHaveLength(3);
  });

  it("the composer says who a message goes to in a shared chat", () => {
    expect(composerPlaceholder(room)).toBe("Message Valeriia and Vexa");
    expect(composerPlaceholder({ you: "171", names: { "171": "Dmitry" }, lines: [] })).toBeNull();
    expect(composerPlaceholder({ you: "1", names: { "1": "A", "2": "Bo", "3": "Cy" }, lines: [] })).toBe("Message Bo, Cy and Vexa");
  });
});

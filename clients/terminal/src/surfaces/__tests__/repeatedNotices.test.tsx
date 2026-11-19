/** A ROUTINE'S REPEATED NOTICE IS ONE LINE (live app.dev, "Support desk": "Couldn't tell how this run
 *  ended: it did not finish within an hour." five times in a row). Consecutive identical lines from
 *  the same author fold into one, with a count and the time range, and open to show each. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { ChatConversation, groupRepeats, interleave, type RoomView } from "../chat";

afterEach(() => cleanup());

const T = (h: number, m: number) => new Date(2026, 9, 14, h, m).getTime() / 1000;
const SAID = "Couldn't tell how this run ended: it did not finish within an hour.";
const room: RoomView = { you: "2", names: { "2": "Dmitry" }, lines: [
  ...[0, 30, 60, 90, 120].map((m, i) => ({ seq: i + 1, author: "vexa", text: SAID, created: T(17, 30) + m * 60 })),
  { seq: 6, author: "2", text: "why?", created: T(19, 45) },
  { seq: 7, author: "vexa", text: SAID, created: T(20, 0) },
] };

describe("repeated notices", () => {
  it("fold consecutive identical lines by the same author, and only those", () => {
    const g = groupRepeats(interleave([], room.lines));
    expect(g.map((x) => (x.kind === "repeat" ? `x${x.lines.length}` : x.kind))).toEqual(["x5", "line", "line"]);
  });

  it("draw as one line with the count and the time range, opening to each", () => {
    const { container } = render(<ChatConversation turns={[]} room={room} />);
    const rep = container.querySelector("[data-turn='repeat']") as HTMLElement;
    expect(rep.textContent).toContain(SAID);
    expect(rep.textContent).toMatch(/×5/);
    expect(rep.textContent).toMatch(/17:30.*19:30/);
    expect(container.querySelectorAll("[data-turn='line']")).toHaveLength(2);
    fireEvent.click(rep.querySelector("[data-repeat-toggle]")!);
    expect(rep.querySelectorAll("[data-repeat-at]")).toHaveLength(5);
  });
});

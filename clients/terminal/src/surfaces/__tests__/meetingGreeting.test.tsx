/** The agent's pre-meeting brief prompt is the agent SPEAKING (founder 2026-10-15): drawn as an agent
 *  turn is, never as centred grey text; a room that is not a meeting's greets with nothing. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { MeetingGreeting } from "../chat";
import { Conversation } from "../agent-window";

afterEach(cleanup);

describe("the meeting's greeting", () => {
  it("renders as an agent line, with the same structure as an agent turn", () => {
    const { container } = render(<MeetingGreeting session="meet-41" />);
    const line = container.querySelector("[data-agent-line]") as HTMLElement;
    expect(line).not.toBeNull();
    expect(line.textContent).toMatch(/booked for your meeting/);
    expect(line.querySelector("strong")?.textContent).toBe("what do you want out of it?");
    const turn = render(<Conversation turns={[{ id: "a", role: "agent", text: "Hello", ops: [] } as never]} busy={false} empty={null} />).container;
    const turnText = turn.querySelector(".t-sm.c-1.lh-normal");
    expect(line.firstElementChild?.className).toBe(turnText?.className);
    expect(line.closest("[style*='center']")).toBeNull();
  });
  it("says nothing in a room that is not a meeting's", () => {
    expect(render(<MeetingGreeting session="pchat-1" />).container.textContent).toBe("");
  });
});

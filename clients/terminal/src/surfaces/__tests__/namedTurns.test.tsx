/** WHO SAID WHAT, AT A GLANCE (founder 2026-10-15, after the Claude app: "user messages and agent
 *  messages must be distinct"). The person's own message is a soft bubble on the right with no
 *  label; the agent's answer is plain full-width text; in a shared chat someone else's line is a
 *  bubble on the left with their initials and name. The names stay for screen readers. Supersedes
 *  the named turns of 2026-10-14. */
import { describe, it, expect, afterEach } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { ChatConversation } from "../chat";

afterEach(cleanup);

describe("my messages and the agent's are distinct", () => {
  it("mine is a right bubble with no visible label; the agent's is plain text with no bubble", async () => {
    const { container } = render(<ChatConversation turns={[
      { id: "u1", role: "user", text: "timing?" },
      { id: "a1", role: "agent", text: "You have been talking for 37 minutes.", ops: [] },
    ] as never} />);
    const mine = container.querySelector('[data-turn="user"]')!;
    expect(mine.classList.contains("vx-turn-mine")).toBe(true);
    expect(mine.querySelector(".vx-bubble-mine")?.textContent).toContain("timing?");
    expect(mine.querySelector("[data-turn-who]")?.className).toBe("vx-sr");          // read aloud, not shown
    const agent = container.querySelector('[data-turn="agent"]')!;
    expect(agent.querySelector(".vx-bubble")).toBeNull();
    expect(agent.querySelector("[data-turn-who]")?.className).toBe("vx-sr");
    await waitFor(() => expect(agent.textContent).toContain("37 minutes"));
  });
});

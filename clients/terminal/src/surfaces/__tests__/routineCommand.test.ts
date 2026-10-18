/** `/routine` asks the agent for a FLOW (ADR-0046): it names the flows tool and no file path, so the
 *  agent never writes a routine file (which nothing reads, and which agent-api refuses). */
import { describe, it, expect } from "vitest";
import { routineCreationPrompt } from "../chat";

describe("the /routine prompt", () => {
  const prompt = routineCreationPrompt("/routine every morning at 9 summarise my unread mail");
  it("names the flows tool and binds the routine to THIS chat", () => {
    expect(prompt).toContain("flows_submit");
    expect(prompt).toContain('"agent_run"');
    expect(prompt).toContain("params.thread");
    expect(prompt).toContain("runs in this chat");
  });
  it("a draft is pointed at the control the person has — the Turn on line — never a Routines board", () => {
    expect(prompt).toContain("awaiting_approval");
    expect(prompt).toContain("Turn on");
    expect(prompt).toContain("never try to turn it on yourself");
  });
  it("carries no file path and no file shape", () => {
    expect(prompt).not.toMatch(/routines\//);
    expect(prompt).not.toMatch(/\.md\b/);
    expect(prompt).not.toMatch(/^enabled:/m);
    expect(prompt).not.toMatch(/frontmatter/i);
  });
  it("asks first when there is nothing to schedule", () => {
    expect(routineCreationPrompt("/routine")).toContain("Ask one concise follow-up");
  });
});

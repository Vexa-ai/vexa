/** UI decision 6b — the agent proposes where a chat writes; only the person's click switches. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@testing-library/react";
import { decideProposal, proposalButtons, proposalQuestion } from "../targetProposal";
import { Conversation, type Turn } from "../agent-window";
import { insideWorkspace } from "../../minutes/chats";

afterEach(() => cleanup());

const P = { workspace: "oenb-vexa-pilot", name: "OeNB × Vexa pilot", current: "biz-1", currentName: "biz",
            reason: "the request is about the OeNB pilot" };

describe("the proposal", () => {
  it("asks in the person's words: Write to OeNB × Vexa pilot instead of biz? [Switch] [Keep biz]", () => {
    expect(proposalQuestion(P)).toBe("Write to OeNB × Vexa pilot instead of biz?");
    expect(proposalButtons(P)).toEqual({ switch: "Switch", keep: "Keep biz" });
  });
  it("Switch moves the target (the person's own call) and tells the waiting agent", () => {
    const d = decideProposal(P, "switch", "pchat-w1");
    expect(d.target).toEqual({ session: "pchat-w1", workspace: "oenb-vexa-pilot" });
    expect(d.ask.display).toBe("Switch to OeNB × Vexa pilot");
    expect(d.ask.prompt).toContain("switched this chat's writes to OeNB × Vexa pilot");
  });
  it("Keep moves nothing", () => {
    const d = decideProposal(P, "keep", "pchat-w1");
    expect(d.target).toBeNull();
    expect(d.ask.display).toBe("Keep biz");
  });
  it("Personal is switched to as no workspace", () => {
    expect(decideProposal({ ...P, workspace: "personal", name: "Personal" }, "switch", "s").target?.workspace).toBe("");
  });
});

describe("the line on the agent's bubble", () => {
  const turn: Turn = { id: "a1", role: "agent", text: "I'll put it in the OeNB pilot.", ops: [], proposal: P };
  it("shows the question with the reason and the two buttons; a click is the only move", () => {
    const onDecide = vi.fn();
    const c = render(<Conversation turns={[turn]} onDecide={onDecide} />).container;
    const line = c.querySelector("[data-target-proposal]")!;
    expect(line.textContent).toContain("Write to OeNB × Vexa pilot instead of biz? — the request is about the OeNB pilot");
    expect(onDecide).not.toHaveBeenCalled();
    fireEvent.click(c.querySelector('[data-proposal-act="switch"]')!);
    expect(onDecide).toHaveBeenCalledWith(turn, "switch");
  });
  it("once decided, says what was chosen and offers no buttons", () => {
    const c = render(<Conversation turns={[{ ...turn, proposal: { ...P, decided: "keep" } } as Turn]} onDecide={() => {}} />).container;
    expect(c.querySelector("[data-proposal-act]")).toBeNull();
    expect(c.querySelector("[data-target-proposal]")?.textContent).toContain("Keep biz");
  });
});

describe("a chat started from inside a workspace writes there", () => {
  it("names the workspace of the page in front, never the desk, the company layer or system", () => {
    expect(insideWorkspace("oenb-vexa-pilot")).toBe("oenb-vexa-pilot");
    expect(insideWorkspace(undefined)).toBe("");
    expect(insideWorkspace("personal")).toBe("");
    expect(insideWorkspace("2")).toBe("");
    expect(insideWorkspace("_global")).toBe("");
    expect(insideWorkspace("_system")).toBe("");
  });
});

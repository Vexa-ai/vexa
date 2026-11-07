/** A STARTER NEVER POSTS ITS INSTRUCTION AS THE PERSON'S MESSAGE (founder, live: "Create a shared
 *  workspace" showed its whole internal prompt — workspace_templates_list, workspace_create,
 *  workspace_invite_link_create, calendar_event_create — as his own line). Every starter, chip and
 *  home action carries a short human line; the record keeps the instruction hidden behind it; no
 *  rendered user line ever contains a tool name. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { ChatConversation, SAY_MARK, shownWords, withSay } from "../chat";
import type { Turn } from "../agent-window";
import { CHECK_EMAIL, SHARED_WORKSPACE, jtbdProposal, prepProposal, setupProposal } from "../../minutes/proposals";
import { homePrompt } from "../../minutes/homeApi";

afterEach(() => cleanup());
const TOOL = /\b[a-z]+(?:_[a-z]+)+\b/;               // workspace_create, gmail_inbox, home_item_update …

const STARTERS = [SHARED_WORKSPACE, CHECK_EMAIL, setupProposal("a@b.c"), prepProposal({ title: "Acme pricing", at: Date.now() + 3600000 }),
  jtbdProposal({ id: "j1", source: "meeting:1", source_label: "Pilot sync", act: "Send the deck", since: "2026-10-01T09:00:00Z", status: "open", by: "post-meeting" } as never)];

describe("every starter", () => {
  it("has a short human line with no tool name in it", () => {
    for (const p of STARTERS) {
      if (!p.kick) continue;
      expect(p.say, p.id).toBeTruthy();
      expect(p.say!, p.id).not.toMatch(TOOL);
    }
  });
  it("is stored as instruction + hidden mark + the line, and only the line is ever drawn", () => {
    const turns: Turn[] = STARTERS.filter((p) => p.kick).map((p, i) => ({ id: `u${i}`, role: "user" as const, text: withSay(p.kick!, p.say) }));
    turns.push({ id: "uh", role: "user", text: withSay(homePrompt("Start a call with Vexa.", "t-call"), "Start a call with Vexa.") });
    const { container } = render(<ChatConversation turns={turns} />);
    const lines = [...container.querySelectorAll(".vx-bubble-mine")].map((e) => e.textContent ?? "");
    expect(lines.length).toBe(turns.length);
    for (const l of lines) expect(l, l).not.toMatch(TOOL);
    expect(lines[0]).toBe("Create a shared workspace.");
    expect(withSay(SHARED_WORKSPACE.kick!, SHARED_WORKSPACE.say)).toContain(SAY_MARK);
    expect(shownWords(withSay("do x with tool_y", "Do x"))).toBe("Do x");
  });
});

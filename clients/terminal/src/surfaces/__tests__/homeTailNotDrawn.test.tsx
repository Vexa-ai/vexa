/** A HOME ACT'S HIDDEN TAIL IS NEVER DRAWN (lab 2026-10-14: a chat showed "Start a voice note. (This
 *  came from your home, item t-voice. When it is done … call home_item_update …)" as the person's
 *  line). The person's short line is drawn; the instruction stays in the record, unrendered. */
import { afterEach, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { ChatConversation } from "../chat";
import type { Turn } from "../agent-window";
import { homePrompt } from "../../minutes/homeApi";

afterEach(() => cleanup());

it("draws the person's words and never the home's instruction", () => {
  const turns: Turn[] = [{ id: "u1", role: "user", text: homePrompt("Start a call with Vexa.", "t-call") }];
  const { container } = render(<ChatConversation turns={turns} />);
  expect(container.textContent).toContain("Start a call with Vexa.");
  expect(container.textContent).not.toMatch(/This came from your home|home_item_update/);
});

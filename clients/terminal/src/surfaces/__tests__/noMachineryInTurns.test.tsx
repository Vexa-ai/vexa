/** NO TURN SHOWS THE MACHINERY (live app.dev: "Update notes" put its whole internal instruction in
 *  the chat as if the person had typed it). Whatever a stored turn or a room line carries, the
 *  conversation never draws the instruction's marker phrases; it draws the act's short line. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { ChatConversation, type RoomView } from "../chat";
import type { Turn } from "../agent-window";

afterEach(() => cleanup());

const LEAK = "Extend: kg/meeting.md\n\nThis is meeting 100's own page and the transcript is embedded in it. Read the page first: " +
  "its frontmatter carries `transcript_cursor`. … read it all — then rewrite the content BETWEEN the " +
  "`<!-- meeting:<key>:start -->` / `:end` markers … set `transcript_cursor` to the `next_index` that read returned.";
const PHRASES = ["<!-- meeting:", "transcript_cursor", "next_index", "BETWEEN the", "[vexa-"];

describe("the conversation never draws an act's instruction", () => {
  it("not as a turn, not as a room line — the short line instead", () => {
    const turns: Turn[] = [{ id: "u1", role: "user", text: LEAK, at: 1 }, { id: "a1", role: "agent", text: "Updated.", ops: [], at: 2 }];
    const room: RoomView = { you: "2", names: { "2": "Dmitry", "60": "Valeriia" }, lines: [{ seq: 1, author: "2", text: LEAK, created: 3 }] };
    const { container } = render(<ChatConversation turns={turns} room={room} />);
    const text = container.textContent ?? "";
    for (const p of PHRASES) expect(text).not.toContain(p);
    expect(text).toContain("Update notes from the transcript");
  });
});

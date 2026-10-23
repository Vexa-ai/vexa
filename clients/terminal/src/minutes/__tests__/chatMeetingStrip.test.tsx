/** A CHAT THAT STARTED A MEETING SHOWS IT (founder, 2026-10-13): the chat header carries the bound
 *  meeting — its state, Stop bot while a bot is in it, and the way to its transcript page — and a
 *  voice note started from a chat binds to that chat instead of opening a chat of its own. */
import { describe, it, expect, vi } from "vitest";
import { bindMeetingToChat } from "../../surfaces/sessionsApi";

// The header chip itself (one "● Live · <meeting>" chip, no Stop bot / Transcript beside the title)
// is pinned in `chatHeader.test.tsx` (founder, 2026-10-14).

describe("binding a meeting started from a chat", () => {
  it("posts the chat and the meeting row", async () => {
    const f = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as unknown as Response);
    globalThis.fetch = f as unknown as typeof fetch;
    await bindMeetingToChat("pchat-1", "301");
    expect(f).toHaveBeenCalledWith("/api/chat/meeting", expect.objectContaining({ method: "POST",
      body: JSON.stringify({ session: "pchat-1", meeting: "301" }) }));
  });
});

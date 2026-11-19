/** "Send now" on a queued row (2026-10-13): the founder's link and "send bot" sat queued behind a long
 *  turn. A queued message row offers to run now; pressing it asks agent-api to stop the running turn
 *  and run that message next. Only a queued, unblocked message the server holds offers it. */
import { describe, it, expect, vi } from "vitest";
import { sendNow, canSendNow } from "../inbox";

describe("send now", () => {
  it("posts the row's id for its session", async () => {
    const f = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as unknown as Response);
    expect(await sendNow("pchat-1", "c-2", f as unknown as typeof fetch)).toBe(true);
    expect(f).toHaveBeenCalledWith("/api/chat/pending/now", expect.objectContaining({ method: "POST",
      body: JSON.stringify({ session: "pchat-1", id: "c-2" }) }));
  });
  it("is false when the message already ran", async () => {
    const f = vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) }) as unknown as Response);
    expect(await sendNow("pchat-1", "c-2", f as unknown as typeof fetch)).toBe(false);
  });
  it("is offered on a queued message row only", () => {
    expect(canSendNow({ id: "c", kind: "message", target: "x", steps: 0, label: "", queued: true, inbox: true })).toBe(true);
    expect(canSendNow({ id: "c", kind: "extend", target: "x", steps: 0, label: "", queued: true, inbox: true })).toBe(false);
    expect(canSendNow({ id: "c", kind: "message", target: "x", steps: 2, label: "", queued: false })).toBe(false);
  });
});

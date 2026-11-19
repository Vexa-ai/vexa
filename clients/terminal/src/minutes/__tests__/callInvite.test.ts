/** Founder 2026-10-16: add people to an in-app call by LINK. The link is this call's chat join link
 *  (scoped to the chat, a day, revocable); the server's join-link tests hold its scope. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { copyCallInvite, hasCallInvite, resetCallInvite, stopCallInvite } from "../callInvite";

afterEach(() => { resetCallInvite(); vi.unstubAllGlobals(); });

describe("copyCallInvite", () => {
  it("mints a day-long link for THIS chat, copies it, and Stop link revokes it", async () => {
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      posts.push({ url: String(url), body: JSON.parse(String(init?.body ?? "{}")) });
      return new Response(JSON.stringify(String(url).endsWith("/revoke") ? { revoked: true } : { id: "lk1", url: "https://app.test/join/abc" }), { status: 200 });
    }));
    const copied: string[] = [];
    expect(await copyCallInvite("s-room1", async (t) => { copied.push(t); })).toBe("copied");
    expect(posts[0]).toEqual({ url: "/api/join-links", body: { target_kind: "chat", target_id: "s-room1", role: "write", max_uses: 50, expires_in_days: 1 } });
    expect(copied).toEqual(["https://app.test/join/abc"]);
    expect(hasCallInvite("s-room1")).toBe(true);
    expect(await stopCallInvite("s-room1")).toBe(true);
    expect(posts[1]).toEqual({ url: "/api/join-links/revoke", body: { target_kind: "chat", target_id: "s-room1", link_id: "lk1" } });
    expect(hasCallInvite("s-room1")).toBe(false);
  });
});

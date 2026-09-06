/** THE RAIL IS THE SERVER'S SESSIONS, MERGED WITH THE LOCAL RECORD (Vexa-ai/vexa#1591).
 *
 *  Founder walk, 2026-09-06. After a morning of work on this instance — the global scaffold, a
 *  meeting chat, several Extend jobs — he signed in again in a new window and got an empty rail:
 *  *"i logged in again and now see no chats and it's starting over again while it has the context"*.
 *  `vexa.minutes.chats` is ONE browser's storage; the server held every one of those conversations.
 *
 *  What these pin is the DIRECTION of the merge, because that is the whole design and it is the
 *  half a wire test cannot see: **local caches, the server owns**. A session the server reports
 *  exists whether or not this browser has heard of it; the reading state — tabs, focus, the page in
 *  front — exists nowhere but here and is never overwritten by a row that does not carry it.
 *
 *  The server half is pinned in `core/agent/tests/test_rail_sessions.py`.
 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  chatsFromSessions, hideChat, loadHidden, mergeChats, meetingIdFromChatId, newChat,
  RAIL_HIDDEN_KEY, railRows, resetChats, visibleRows,
  type Chat, type ServerSession,
} from "../chats";

const NOW = Date.parse("2026-09-06T12:00:00Z");
/** the index stores epoch SECONDS */
const secs = (iso: string) => Date.parse(iso) / 1000;

const session = (over: Partial<ServerSession> & { session: string }): ServerSession => ({
  title: null, created: secs("2026-09-06T08:00:00Z"), last_active: secs("2026-09-06T09:00:00Z"),
  workspaces: null, scaffold: null, touched: true, ...over,
});

beforeEach(() => {
  localStorage.clear();
});

// ── the derivation ───────────────────────────────────────────────────────────────────────────

describe("a server session becomes a rail row", () => {
  it("carries the title, the mounts, the record it was composed from, and its recency", () => {
    const [c] = chatsFromSessions([session({
      session: "scaffold-SC1", title: "Welcome",
      workspaces: ["_global", "u_priya"], scaffold: { kind: "first-visit", id: "SC1" },
    })], NOW);
    expect(c.id).toBe("scaffold-SC1");
    expect(c.label).toBe("Welcome");
    expect(c.workspaces).toEqual(["_global", "u_priya"]);
    expect(c.scaffold).toEqual({ kind: "first-visit", id: "SC1" });
    expect(c.lastActivityAt).toBe(Date.parse("2026-09-06T09:00:00Z"));
    expect(c.touched).toBe(true);
  });

  it("reads a meeting off the session id and leaves the LABEL to the meeting", () => {
    // `meet-<row>` is this client's own naming, so the inverse lives here and the server sends no
    // `meeting` field. An empty label is not missing data: railRows names the row from the meeting,
    // so it follows a rename instead of freezing the first turn's title.
    expect(meetingIdFromChatId("meet-42")).toBe("42");
    expect(meetingIdFromChatId("scaffold-SC1")).toBeNull();
    const [c] = chatsFromSessions([session({ session: "meet-42", title: "whatever" })], NOW);
    expect(c.meeting).toBe("42");
    expect(c.label).toBe("");
  });

  it("treats the index's default title — the session id itself — as the placeholder it is", () => {
    const [c] = chatsFromSessions([session({ session: "pchat-abc", title: "pchat-abc" })], NOW);
    expect(c.label).toBe("Chat");
  });

  it("degrades on every optional field rather than dropping the row", () => {
    const [c] = chatsFromSessions([{ session: "pchat-abc" } as ServerSession], NOW);
    expect(c.workspaces).toEqual(["personal", "_global"]);
    expect(c.scaffold).toBeUndefined();
    // absent `touched` → a conversation that happened. The failure being fixed is chats that do not
    // show, so the fallback goes towards showing.
    expect(c.touched).toBe(true);
    expect(c.lastActivityAt).toBe(NOW);
  });

  it("drops a half scaffold record instead of repairing it (F37)", () => {
    const [c] = chatsFromSessions([session({ session: "s", scaffold: { kind: "admin-setup" } })], NOW);
    expect(c.scaffold).toBeUndefined();
  });

  it("refuses the two ids the rail used to PLANT — including `main`, the default thread", () => {
    // F34: the founder deleted "Personal" and "Organisation setup" by ruling, and `pruneStale`
    // removes them on every load. `main` is also `units.DEFAULT_CHAT_SESSION`, so it is the session
    // any un-named chat lands in — admitting it would make the row flicker back on every fetch and
    // vanish on every reload, which is worse than either answer.
    expect(chatsFromSessions([session({ session: "main", title: "Personal" }),
                              session({ session: "org-setup" })], NOW)).toEqual([]);
  });

  it("puts the newest first once the rows reach the rail", () => {
    const chats = chatsFromSessions([
      session({ session: "old", title: "Old", last_active: secs("2026-09-05T09:00:00Z") }),
      session({ session: "new", title: "New", last_active: secs("2026-09-06T11:00:00Z") }),
    ], NOW);
    expect(railRows(chats, [], NOW).map((r) => r.label)).toEqual(["New", "Old"]);
    // …and they are VISIBLE under the default filter, which is the acceptance criterion itself:
    // a rail that lists them behind an "all" chip is still an empty rail to the person reading it.
    expect(visibleRows(railRows(chats, [], NOW), false).map((r) => r.label)).toEqual(["New", "Old"]);
  });

  it("a thread with nothing but machinery in it stays behind the filter", () => {
    const chats = chatsFromSessions([session({ session: "pchat-x", title: "Welcome", touched: false })], NOW);
    expect(visibleRows(railRows(chats, [], NOW), false)).toEqual([]);
    expect(visibleRows(railRows(chats, [], NOW), true)).toHaveLength(1);
  });
});

// ── the merge ────────────────────────────────────────────────────────────────────────────────

const local = (over: Partial<Chat> & { id: string }): Chat => ({
  label: "Local", workspaces: ["personal", "_global"], artifacts: [],
  touched: true, createdAt: NOW - 10_000, lastActivityAt: NOW - 10_000, ...over,
});

describe("merging the server's rail into the stored one", () => {
  it("keeps the reading state, which exists nowhere else", () => {
    const stored = local({
      id: "pchat-abc",
      artifacts: [{ path: "README.md", label: "Desk", desk: true }],
      focus: "|README.md",
      view: { path: "notes.md", label: "notes.md" },
    });
    const [c] = mergeChats([stored], chatsFromSessions([session({ session: "pchat-abc" })], NOW));
    expect(c.artifacts).toEqual(stored.artifacts);
    expect(c.focus).toBe("|README.md");
    expect(c.view).toEqual(stored.view);
  });

  it("takes the LATER activity from either side", () => {
    // the other browser's turn is on the server; this browser's turn is not there yet
    const stored = local({ id: "s", lastActivityAt: Date.parse("2026-09-06T10:00:00Z") });
    const ahead = chatsFromSessions([session({ session: "s", last_active: secs("2026-09-06T11:00:00Z") })], NOW);
    expect(mergeChats([stored], ahead)[0].lastActivityAt).toBe(Date.parse("2026-09-06T11:00:00Z"));
    const behind = chatsFromSessions([session({ session: "s", last_active: secs("2026-09-06T08:00:00Z") })], NOW);
    expect(mergeChats([stored], behind)[0].lastActivityAt).toBe(Date.parse("2026-09-06T10:00:00Z"));
  });

  it("lets a real name replace a placeholder, from whichever side holds it", () => {
    const stored = local({ id: "s", label: "New chat" });
    expect(mergeChats([stored], chatsFromSessions([session({ session: "s", title: "Plan the launch" })], NOW))[0].label)
      .toBe("Plan the launch");
    const named = local({ id: "s", label: "What Priya asked for" });
    expect(mergeChats([named], chatsFromSessions([session({ session: "s", title: "Plan the launch" })], NOW))[0].label)
      .toBe("What Priya asked for");
  });

  it("adds what this browser has never heard of, and keeps what the server has not seen", () => {
    const drafted = local({ id: "pchat-local" });
    const merged = mergeChats([drafted], chatsFromSessions([session({ session: "meet-42" })], NOW));
    expect(merged.map((c) => c.id).sort()).toEqual(["meet-42", "pchat-local"]);
  });

  it("a person's own mount set survives — the server's is the fallback, not the verdict", () => {
    const rebound = local({ id: "s", workspaces: ["personal", "_global", "grp-showb"] });
    const server = chatsFromSessions([session({ session: "s", workspaces: ["_global"] })], NOW);
    expect(mergeChats([rebound], server)[0].workspaces).toEqual(["personal", "_global", "grp-showb"]);
    expect(mergeChats([], server)[0].workspaces).toEqual(["_global"]);
  });

  it("touched is either side's, because both are evidence a person wrote", () => {
    const untouched = local({ id: "s", touched: false });
    expect(mergeChats([untouched], chatsFromSessions([session({ session: "s", touched: true })], NOW))[0].touched).toBe(true);
    const touched = local({ id: "s", touched: true });
    expect(mergeChats([touched], chatsFromSessions([session({ session: "s", touched: false })], NOW))[0].touched).toBe(true);
  });

  it("a row the reader DELETED does not come back on the next sign-in", () => {
    // `deleteChat` has always meant "off my rail", not "destroy the thread" — its own comment says
    // the agent session stays on the server. With the rail derived from those sessions, the delete
    // has to be remembered or the fetch simply undoes it.
    hideChat("meet-42");
    expect(loadHidden()).toEqual(["meet-42"]);
    const server = chatsFromSessions([session({ session: "meet-42" }), session({ session: "meet-43" })], NOW);
    expect(mergeChats([], server, loadHidden()).map((c) => c.id)).toEqual(["meet-43"]);
  });

  it("…and the deletes go with the rail when a different person signs in on this browser", () => {
    // The tombstone list is keyed to nobody. Inherited, it would silently subtract rows from the
    // NEXT person's server sessions — chats they never touched, missing, with no way to tell.
    hideChat("meet-42");
    resetChats();
    expect(localStorage.getItem(RAIL_HIDDEN_KEY)).toBeNull();
    expect(mergeChats([], chatsFromSessions([session({ session: "meet-42" })], NOW), loadHidden()))
      .toHaveLength(1);
  });

  it("is idempotent — merging the same answer twice changes nothing", () => {
    const server = chatsFromSessions([session({ session: "s", title: "Plan" })], NOW);
    const once = mergeChats([newChat("New chat", ["personal", "_global"], { id: "s", now: NOW })], server);
    expect(mergeChats(once, server)).toEqual(once);
  });
});

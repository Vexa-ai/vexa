/** F38 — A CHAT IS NAMED BY ITS FIRST HUMAN TURN. Founder ruling 2026-09-02.
 *
 *  He worked a `+` chat for many turns — created a shared workspace in it, asked for research — and
 *  the rail still read **"New chat"**. A conversation with a dozen turns and no name is a row nobody
 *  can find again tomorrow.
 *
 *  It lands in the same write as the record itself: F35 says a `+` chat is not persisted until its
 *  first human turn, so the moment it becomes real is the moment it has something to be called. One
 *  write, not a record now and a name later.
 *
 *  The three refusals are the interesting half, and each is a rule rather than a guard — see
 *  `nameChat`'s own comment for why. */
import { describe, expect, it } from "vitest";
import { CHAT_TITLE_MAX, isPlaceholderLabel, nameChat, nameFromTurn, newChat, titleFromTurn, type Chat } from "../chats";

const T0 = Date.UTC(2026, 8, 2, 12, 5, 0);
const draft = (over: Partial<Chat> = {}): Chat =>
  ({ ...newChat("New chat", ["personal", "_global"], { touched: false, now: T0 }), ...over });

describe("titleFromTurn — one line, trimmed, cut with an ellipsis", () => {
  it("takes the sentence as the person typed it", () => {
    expect(titleFromTurn("set up a shared workspace for the daily")).toBe("set up a shared workspace for the daily");
  });

  it("collapses a multi-line paste into ONE line — a rail row is one line high", () => {
    expect(titleFromTurn("  create a group\n\nfor the daily   standup ")).toBe("create a group for the daily standup");
  });

  it(`cuts at ${CHAT_TITLE_MAX} with an ellipsis rather than truncating mid-render`, () => {
    const long = "research every company in the pipeline and write me a one page brief on each";
    const out = titleFromTurn(long);
    expect(out.length).toBeLessThanOrEqual(CHAT_TITLE_MAX);
    expect(out.endsWith("…")).toBe(true);
    expect(long.startsWith(out.slice(0, -1).trimEnd())).toBe(true);
  });

  it("an empty turn names nothing — better a placeholder than a blank row", () => {
    expect(titleFromTurn("")).toBe("");
    expect(titleFromTurn("   \n  ")).toBe("");
  });
});

describe("nameChat — what may be renamed, and what may not", () => {
  it("a `+` chat takes the name from the turn", () => {
    expect(nameChat(draft(), "set up a shared workspace").label).toBe("set up a shared workspace");
  });

  it("…and so does one that normalised down to the other placeholder", () => {
    expect(nameChat(draft({ label: "Chat" }), "what came out of the sync?").label).toBe("what came out of the sync?");
  });

  it("A SCAFFOLDED CHAT KEEPS ITS OWN TITLE — the record already named it, deliberately", () => {
    // Overwriting it would swap a considered name for whatever the person happened to type first.
    // It is also the rule agent-api applies to the session title, so the two halves agree by
    // construction rather than by coincidence.
    const c = draft({ label: "New chat", scaffold: { kind: "admin-setup", id: "S1" } });
    expect(nameChat(c, "hello").label).toBe("New chat");
    expect(nameChat(c, "hello")).toBe(c);
  });

  it("A MEETING CHAT IS NAMED BY ITS MEETING — freezing a sentence on it would undo the rename", () => {
    const c = draft({ label: "", meeting: "m-post" });
    expect(nameChat(c, "what did we decide?")).toBe(c);
  });

  it("a name a human chose stands", () => {
    const c = draft({ label: "Q3 planning" });
    expect(nameChat(c, "actually let's talk about hiring")).toBe(c);
  });

  it("an empty turn leaves the placeholder rather than blanking the row", () => {
    expect(nameChat(draft(), "   ").label).toBe("New chat");
  });

  it("names ONCE — the second turn does not rewrite the first turn's name", () => {
    const named = nameChat(draft(), "set up a shared workspace");
    expect(nameChat(named, "and invite the team").label).toBe("set up a shared workspace");
  });
});

describe("nameFromTurn — the same rule over the stored list", () => {
  it("touches only the chat the turn was in", () => {
    const a = draft({ id: "a" }), b = draft({ id: "b", label: "Q3 planning" });
    const out = nameFromTurn([a, b], "a", "research Acme");
    expect(out.map((c) => c.label)).toEqual(["research Acme", "Q3 planning"]);
  });

  it("a session with no record is a no-op, not a throw", () => {
    const list = [draft({ id: "a" })];
    expect(nameFromTurn(list, "nobody", "hello").map((c) => c.label)).toEqual(["New chat"]);
  });
});

describe("isPlaceholderLabel — which names may be replaced", () => {
  it("the placeholders the product itself writes", () => {
    for (const l of ["New chat", "new chat", "Chat", "", "   "]) expect(isPlaceholderLabel(l)).toBe(true);
  });
  it("anything a human, a meeting or a scaffold put there", () => {
    for (const l of ["Personal", "Standup", "setup global", "Chat with Ada"]) expect(isPlaceholderLabel(l)).toBe(false);
  });
});

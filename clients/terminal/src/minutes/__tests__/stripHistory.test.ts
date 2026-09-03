/** PRD DECISION 28 (+ the founder's amendments) — the strip is a HISTORY BAR.
 *
 *  Screenshot 1: seven tabs after a few chip clicks, the path shown twice. Screenshot 2: the strip
 *  scrolled off the edge. *"ensure these tabs are sorted left to right based on last used — as a
 *  history bar"*, and *"pin is per chat"*.
 *
 *  So the strip has three tiers, left to right: the chat's HOME · its PINS · history oldest→newest,
 *  with the page you are on at the right edge. Everything below is that rule.
 */
import { describe, expect, it } from "vitest";
import {
  artifactKey, chatHistory, collapseUnpinned, forgetHistory, homeEntry, HISTORY_CAP,
  orderHistory, touchHistory, withHome, type Artifact, type Chat,
} from "../chats";

const A = (path: string, over: Partial<Artifact> = {}): Artifact =>
  ({ path, label: path.replace(/\.md$/, ""), ...over });
const paths = (l: Artifact[]) => l.map((a) => (a.slug ? `${a.slug}/` : "") + a.path);

describe("orderHistory — home, then pins, then oldest → newest", () => {
  it("puts the current page at the RIGHT edge and the home at the left", () => {
    const out = orderHistory([
      A("b.md", { at: 2 }), A("home.md", { desk: true }), A("p.md", { pinned: true }), A("a.md", { at: 1 }),
    ]);
    expect(paths(out)).toEqual(["home.md", "p.md", "a.md", "b.md"]);
  });
});

describe("touchHistory", () => {
  it("moves a page already in the strip to the right end — never a second chip", () => {
    let l = [A("a.md", { at: 1 }), A("b.md", { at: 2 }), A("c.md", { at: 3 })];
    l = touchHistory(l, A("a.md"), 4);
    expect(paths(l)).toEqual(["b.md", "c.md", "a.md"]);
    expect(l).toHaveLength(3);            // dedup, not append
  });

  it("caps at 12 by dropping the OLDEST, and never a pin", () => {
    let l: Artifact[] = [A("pinned.md", { pinned: true, at: 0 })];
    for (let i = 1; i <= HISTORY_CAP + 3; i++) l = touchHistory(l, A(`f${i}.md`), i);
    expect(l.filter((a) => !a.pinned)).toHaveLength(HISTORY_CAP);
    expect(l.some((a) => a.path === "pinned.md")).toBe(true);   // a cap that could evict a pin
    expect(l.some((a) => a.path === "f1.md")).toBe(false);      // would make pinning a suggestion
    expect(paths(l)[paths(l).length - 1]).toBe(`f${HISTORY_CAP + 3}.md`);
  });

  it("navigating to a PIN keeps it pinned and keeps it at the left edge", () => {
    let l = [A("p.md", { pinned: true, at: 1 }), A("a.md", { at: 2 })];
    l = touchHistory(l, A("p.md"), 3);
    expect(l.find((a) => a.path === "p.md")?.pinned).toBe(true);
    expect(paths(l)).toEqual(["p.md", "a.md"]);
  });

  it("the home is never evicted, however much is opened", () => {
    let l = withHome([], []);
    for (let i = 1; i <= HISTORY_CAP + 5; i++) l = touchHistory(l, A(`f${i}.md`), i);
    expect(l.some((a) => a.desk)).toBe(true);
    expect(l[0].desk).toBe(true);
  });
});

describe("forgetHistory — × forgets, except the home", () => {
  it("forgets an ordinary entry", () => {
    const l = [A("a.md", { at: 1 }), A("b.md", { at: 2 })];
    expect(paths(forgetHistory(l, artifactKey(A("a.md"))))).toEqual(["b.md"]);
  });

  it("refuses to forget the home — it is the product's entry, not the reader's", () => {
    const l = withHome([A("a.md", { at: 1 })], []);
    const home = l.find((a) => a.desk)!;
    expect(forgetHistory(l, artifactKey(home)).some((a) => a.desk)).toBe(true);
  });
});

describe("homeEntry / withHome — the chat's home follows its mounts", () => {
  it("no group → the reader's own desk README", () => {
    expect(homeEntry(["_global", "personal"])).toEqual({ path: "README.md", label: "Desk", desk: true });
    // `u_*` is the SERVER's name for a person's desk — not a group, or every scaffolded chat
    // would be "at home" in the reader's own desk under its server name.
    expect(homeEntry(["_global", "u_priya"]).slug).toBeUndefined();
  });

  it("a group in the mounts → THAT group's README instead", () => {
    expect(homeEntry(["_global", "u_priya", "grp-showb"]))
      .toEqual({ path: "README.md", slug: "grp-showb", label: "grp-showb", desk: true });
  });

  it("never duplicates a README the strip already had — it promotes it", () => {
    const out = withHome([A("README.md", { slug: "grp-showb", at: 5 })], ["_global", "grp-showb"]);
    expect(out.filter((a) => a.path === "README.md")).toHaveLength(1);
    expect(out[0].desk).toBe(true);
  });

  it("is idempotent", () => {
    const once = withHome([A("a.md", { at: 1 })], ["_global", "grp-showb"]);
    expect(withHome(once, ["_global", "grp-showb"])).toEqual(once);
  });
});

describe("chatHistory — the GET-able shape for the desk README (decision 26.4)", () => {
  it("newest first, with the workspace, path, title and stamp", () => {
    const c = { artifacts: [A("a.md", { at: 1 }), A("b.md", { slug: "acme", at: 9 })] } as Chat;
    expect(chatHistory(c)).toEqual([
      { workspace: "acme", path: "b.md", title: "b", at: 9 },
      { workspace: "", path: "a.md", title: "a", at: 1 },
    ]);
  });

  it("omits the meeting canvas — it is not a document anything can list", () => {
    const c = { artifacts: [A("42", { kind: "meeting", at: 3 }), A("a.md", { at: 1 })] } as Chat;
    expect(chatHistory(c).map((h) => h.path)).toEqual(["a.md"]);
  });
});

describe("collapseUnpinned — the one-time migration, as amended", () => {
  it("a pre-28 pile becomes ORDERED history, not a deletion", () => {
    // the amendment reframed these entries: they are history that was never ordered or capped,
    // so they are kept and put in order rather than thrown away
    const c = collapseUnpinned({
      id: "c1", label: "c", workspaces: [], createdAt: 1, lastActivityAt: 1,
      artifacts: [A("a.md"), A("b.md"), A("c.md")], focus: "|b.md",
    } as Chat);
    expect(paths(c.artifacts)).toEqual(["a.md", "c.md", "b.md"]);   // the front page lands right
    expect(c.view?.path).toBe("b.md");
  });

  it("caps a pre-28 pile that is over the limit", () => {
    const many = Array.from({ length: HISTORY_CAP + 4 }, (_, i) => A(`f${i}.md`));
    const c = collapseUnpinned({
      id: "c1", label: "c", workspaces: [], createdAt: 1, lastActivityAt: 1, artifacts: many,
    } as Chat);
    expect(c.artifacts).toHaveLength(HISTORY_CAP);
  });

  it("a scaffold's tabs are PINNED — declared cannot be told from clicked, so keep", () => {
    const c = collapseUnpinned({
      id: "c1", label: "c", workspaces: [], createdAt: 1, lastActivityAt: 1,
      scaffold: { kind: "admin-setup", id: "S1" },
      artifacts: [A("README.md", { slug: "_global" }), A("MISSING.md", { slug: "_global" })],
    } as Chat);
    expect(c.artifacts.every((a) => a.pinned)).toBe(true);
  });

  it("is idempotent", () => {
    const base = { id: "c1", label: "c", workspaces: [], createdAt: 1, lastActivityAt: 1,
      artifacts: [A("a.md"), A("b.md")], focus: "|a.md" } as Chat;
    const once = collapseUnpinned(base);
    expect(collapseUnpinned(once)).toEqual(once);
  });
});

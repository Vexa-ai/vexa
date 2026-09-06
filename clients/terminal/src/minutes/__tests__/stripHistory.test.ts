/** PRD DECISION 28, AS RULED ON 2026-09-06 — the strip is TABS PLUS ONE PREVIEW SLOT.
 *
 *  Screenshot 1: seven tabs after a few chip clicks, the path shown twice. Screenshot 2: the strip
 *  scrolled off the edge. The first answer was a history bar — *"ensure these tabs are sorted left
 *  to right based on last used"* — and the walk of 2026-09-06 found it still minting tabs nobody
 *  had asked for: *"no need to create tabs, unless there is a pinned tab. Use obsidian rule for
 *  that and tab icon is on tab."*
 *
 *  So the strip has three tiers, left to right: the chat's HOME · its PINS · the ONE preview slot,
 *  which is the page you are on and which the next page you open replaces. Everything below is
 *  that rule.
 */
import { describe, expect, it } from "vitest";
import {
  artifactKey, chatHistory, collapseUnpinned, forgetHistory, homeEntry, PREVIEW_CAP,
  orderHistory, togglePinned, touchHistory, withHome, type Artifact, type Chat,
} from "../chats";

const A = (path: string, over: Partial<Artifact> = {}): Artifact =>
  ({ path, label: path.replace(/\.md$/, ""), ...over });
const paths = (l: Artifact[]) => l.map((a) => (a.slug ? `${a.slug}/` : "") + a.path);

describe("orderHistory — home, then pins, then the preview", () => {
  it("puts the current page at the RIGHT edge and the home at the left", () => {
    const out = orderHistory([
      A("b.md", { at: 2 }), A("home.md", { desk: true }), A("p.md", { pinned: true }), A("a.md", { at: 1 }),
    ]);
    expect(paths(out)).toEqual(["home.md", "p.md", "a.md", "b.md"]);
  });
});

describe("touchHistory — navigating REPLACES the preview (founder: use obsidian rule)", () => {
  it("a second page does not stand beside the first — it takes its place", () => {
    let l = withHome([], []);
    l = touchHistory(l, A("a.md"), 1);
    expect(paths(l)).toEqual(["README.md", "a.md"]);
    l = touchHistory(l, A("b.md"), 2);
    expect(paths(l)).toEqual(["README.md", "b.md"]);      // …and a.md is not a tab, it never was
  });

  it("browsing four documents leaves ONE, which is the screenshot this ruling came from", () => {
    let l = withHome([], []);
    for (const n of ["objectives", "structure", "missing", "principles"]) l = touchHistory(l, A(`${n}.md`), 1);
    expect(paths(l)).toEqual(["README.md", "principles.md"]);
    expect(l.filter((a) => !a.pinned && !a.desk)).toHaveLength(PREVIEW_CAP);
  });

  it("the preview slot is the only thing that gives — a pin is never evicted", () => {
    let l: Artifact[] = [A("pinned.md", { pinned: true, at: 0 })];
    for (let i = 1; i <= 5; i++) l = touchHistory(l, A(`f${i}.md`), i);
    expect(l.some((a) => a.path === "pinned.md")).toBe(true);   // a cap that could evict a pin
    expect(l.find((a) => a.path === "pinned.md")!.pinned).toBe(true);
    expect(paths(l)).toEqual(["pinned.md", "f5.md"]);
  });

  it("dedups rather than appending: re-opening the page in front is not a second entry", () => {
    let l = withHome([], []);
    l = touchHistory(l, A("a.md"), 1);
    l = touchHistory(l, A("a.md"), 2);
    expect(paths(l)).toEqual(["README.md", "a.md"]);
  });

  it("navigating to a PIN keeps it pinned and keeps it at the left edge", () => {
    let l = [A("p.md", { pinned: true, at: 1 }), A("a.md", { at: 2 })];
    l = touchHistory(l, A("p.md"), 3);
    expect(l.find((a) => a.path === "p.md")?.pinned).toBe(true);
    expect(paths(l)).toEqual(["p.md", "a.md"]);
  });

  it("the home is never evicted, however much is opened", () => {
    let l = withHome([], []);
    for (let i = 1; i <= 6; i++) l = touchHistory(l, A(`f${i}.md`), i);
    expect(l.some((a) => a.desk)).toBe(true);
    expect(l[0].desk).toBe(true);
  });
});

/** THE PIN PROMOTES A PREVIEW TO A TAB — and unpinning has two answers (founder: *"tab icon is on
 *  tab"*). The control is per tab now, so the rule behind it has to say what happens to a pin that
 *  is NOT the page in front, which the old header control could not even express. */
describe("togglePinned", () => {
  const KEY = artifactKey(A("a.md"));

  it("a pin makes it a tab — and the next page previews beside it instead of replacing it", () => {
    let l = withHome([], []);
    l = touchHistory(l, A("a.md"), 1);
    l = togglePinned(l, KEY, true, 2);
    expect(l.find((x) => x.path === "a.md")!.pinned).toBe(true);

    l = touchHistory(l, A("b.md"), 3);
    l = touchHistory(l, A("c.md"), 4);
    expect(paths(l)).toEqual(["README.md", "a.md", "c.md"]);    // the tab stayed; b.md was preview
  });

  it("unpinning the tab IN FRONT hands it back to the preview slot — you are still reading it", () => {
    let l = withHome([], []);
    l = touchHistory(l, A("a.md"), 1);
    l = togglePinned(l, KEY, true, 2);
    l = togglePinned(l, KEY, true, 3);
    expect(paths(l)).toEqual(["README.md", "a.md"]);
    expect(l.find((x) => x.path === "a.md")!.pinned).toBeFalsy();
  });

  it("unpinning a tab BEHIND the one in front drops it — there is nothing else for it to be", () => {
    let l = withHome([A("a.md", { pinned: true, at: 1 })], []);
    l = touchHistory(l, A("b.md"), 2);
    l = togglePinned(l, KEY, false, 3);
    expect(paths(l)).toEqual(["README.md", "b.md"]);            // never two unpinned entries
  });

  it("the home is not a pin, so it does not toggle", () => {
    const l = withHome([], []);
    expect(togglePinned(l, artifactKey(homeEntry([])), true, 2)).toEqual(l);
  });

  it("a page that is not in the strip is not invented", () => {
    const l = withHome([], []);
    expect(togglePinned(l, artifactKey(A("gone.md")), true, 2)).toEqual(l);
  });
});

describe("forgetHistory — × drops a tab, except the home", () => {
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

describe("collapseUnpinned — the one-time migration, as amended and as ruled", () => {
  it("a pre-28 pile collapses to the page the reader was ON, which becomes the view", () => {
    // The first ruling deleted these entries; the amendment kept them as history; the 2026-09-06
    // ruling removes the history tier, so what survives is the ONE page in front. It is identified
    // from `focus` and not from "the last one stored" — the difference is whether a reader comes
    // back to the document they were reading or to whichever tab happened to be written last.
    const c = collapseUnpinned({
      id: "c1", label: "c", workspaces: [], createdAt: 1, lastActivityAt: 1,
      artifacts: [A("a.md"), A("b.md"), A("c.md")], focus: "|b.md",
    } as Chat);
    expect(paths(c.artifacts)).toEqual(["b.md"]);
    expect(c.artifacts).toHaveLength(PREVIEW_CAP);
    expect(c.view?.path).toBe("b.md");
  });

  it("a pile with no focus at all keeps the last one stored — the best answer there is", () => {
    const many = Array.from({ length: 5 }, (_, i) => A(`f${i}.md`));
    const c = collapseUnpinned({
      id: "c1", label: "c", workspaces: [], createdAt: 1, lastActivityAt: 1, artifacts: many,
    } as Chat);
    expect(paths(c.artifacts)).toEqual(["f4.md"]);
    expect(c.view?.path).toBe("f4.md");
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

/** THE NAVIGATOR'S DECISIONS — the ones with a plausible wrong answer.
 *
 *  Every claim here is about a rule that would look fine on screen if it were broken: a hide list
 *  that hides one folder too many or one too few; an order that is alphabetical because nobody
 *  said otherwise; a cap that counts groups instead of hits and so starves the second workspace;
 *  a filter that matches paths and returns a workspace when someone typed a filename.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  frontmatterSaysTemplate, humanPaths, isMachinery, isMachineryEntry,
} from "../machinery";
import {
  MAX_HITS, NAV_OPEN_KEY, buildWorkspaces, filterByName, loadNavOpen, parseQuery,
  referencedWorkspaceIds, saveNavOpen, treeFrom, type NavWorkspace,
} from "../navigatorApi";
import { VIEW_NAVIGATE_EVENT, navigateView, pageForDocRef, viewSlotFor } from "../roomView";
import { artifactKey, touchHistory } from "../chats";
import type { ActiveMount } from "../../surfaces/workspaceApi";

const mount = (over: Partial<ActiveMount> & { slug: string }): ActiveMount => ({
  repo: null, ref: null, role: "shared", path: `/workspaces/${over.slug}`, write: true, primary: false,
  ...over,
});

describe("the hide list — human files only (decision 27.2)", () => {
  it("hides the agent's instructions, the liquid layer, and the templates", () => {
    for (const p of ["CLAUDE.md", "flows", "flows/personal.md", "skills/sell/SKILL.md",
                     "routines/daily.md", "views/board.md", "policy/members.json",
                     "kg/templates", "kg/templates/person.md"]) {
      expect(isMachinery(p), p).toBe(true);
    }
  });

  it("hides dotfiles and anything under a dot-directory", () => {
    expect(isMachinery(".scaffolded")).toBe(true);
    expect(isMachinery(".claude/settings.json")).toBe(true);
    expect(isMachinery("kg/.obsidian/workspace.json")).toBe(true);
  });

  it("hides nothing else — a hide list that guesses hides tomorrow's write-back", () => {
    for (const p of ["README.md", "kg/entities/person/dmitry.md", "drafts/2026-09-01-prd.md",
                     "kg", "kg/entities", "notes/policy-notes.md", "meetings/2026-09-02.md"]) {
      expect(isMachinery(p), p).toBe(false);
    }
  });

  it("`CLAUDE.md` is machinery at the ROOT — a draft ABOUT it is a draft", () => {
    expect(isMachinery("CLAUDE.md")).toBe(true);
    expect(isMachinery("drafts/CLAUDE.md")).toBe(false);
  });

  it("a folder listing asks about prefix + name, and gets the same answer", () => {
    expect(isMachineryEntry("", "flows")).toBe(true);
    expect(isMachineryEntry("kg", "templates")).toBe(true);
    expect(isMachineryEntry("kg", "entities")).toBe(false);
    expect(isMachineryEntry("drafts", "CLAUDE.md")).toBe(false);
  });

  it("humanPaths keeps order and drops the rest", () => {
    expect(humanPaths(["README.md", "flows/a.md", "kg/x.md", ".git/HEAD"]))
      .toEqual(["README.md", "kg/x.md"]);
  });

  it("`template: true` in frontmatter is a template; the word in prose is not", () => {
    expect(frontmatterSaysTemplate("---\ntemplate: true\n---\n# Person\n")).toBe(true);
    expect(frontmatterSaysTemplate("---\nid: p1\ntemplate: false\n---\n")).toBe(false);
    expect(frontmatterSaysTemplate("# Notes\n\ntemplate: true is a line of prose\n")).toBe(false);
    expect(frontmatterSaysTemplate(null)).toBe(false);
  });
});

describe("which workspaces are listed, and in what order (decisions 26–27)", () => {
  const active: ActiveMount[] = [
    mount({ slug: "seed", role: "primary", primary: true, name: "Dmitry's desk", path: "/workspaces/u-7" }),
    mount({ slug: "_global", name: "Helm Bank" }),
    mount({ slug: "zebra-team" }),
    mount({ slug: "acme-kg", name: "Acme" }),
    mount({ slug: "_system" }),
  ];

  it("desk first, then `_global`, then the groups by name", () => {
    const ws = buildWorkspaces({ active, subject: "u-7" });
    expect(ws.map((w) => w.name)).toEqual(["Dmitry's desk", "Helm Bank", "Acme", "zebra-team"]);
    expect(ws.map((w) => w.kind)).toEqual(["desk", "global", "group", "group"]);
  });

  it("the desk is the NO-SLUG read — every other row carries its slug", () => {
    const ws = buildWorkspaces({ active, subject: "u-7" });
    expect(ws[0]).toMatchObject({ key: "desk", slug: undefined, readable: true });
    expect(ws[1].slug).toBe("_global");
  });

  it("`_global` wears the company name when the mount knows one, its slug when it does not", () => {
    expect(buildWorkspaces({ active, subject: "u-7" })[1].name).toBe("Helm Bank");
    const anon = active.map((m) => (m.slug === "_global" ? { ...m, name: null } : m));
    expect(buildWorkspaces({ active: anon, subject: "u-7" })[1].name).toBe("_global");
  });

  it("`_system` is never listed — always mounted, never a place to read files", () => {
    expect(buildWorkspaces({ active, subject: "u-7" }).some((w) => w.key === "_system")).toBe(false);
  });

  it("a membership that is not mounted is still the reader's to open", () => {
    const ws = buildWorkspaces({ active, subject: "u-7", memberships: [{ workspace_id: "parked-team", role: "viewer" }] });
    expect(ws.find((w) => w.key === "parked-team")).toMatchObject({ readable: true, kind: "group" });
  });

  it("a workspace the reader is not in is LISTED, greyed — never hidden, never an error", () => {
    const ws = buildWorkspaces({
      active, subject: "u-7",
      registry: [{ id: "legal-room", name: "Legal" }, { id: "acme-kg", name: "Acme" }],
    });
    const legal = ws.find((w) => w.key === "legal-room");
    expect(legal).toMatchObject({ readable: false, name: "Legal" });
    // a registry row the reader DOES have stays readable — the registry never demotes a membership
    expect(ws.find((w) => w.key === "acme-kg")?.readable).toBe(true);
    // and the greyed rows sit after the readable ones
    expect(ws[ws.length - 1].key).toBe("legal-room");
  });

  it("with nothing at all there is still a desk — it is the workspace that always exists", () => {
    expect(buildWorkspaces({})).toEqual([{ key: "desk", slug: undefined, kind: "desk", readable: true, name: "Desk" }]);
  });

  it("reads decision 26.2's two cross-workspace link forms out of the desk", () => {
    const desk = "See [[ws:legal-room/e-12]] and [the plan](/w/deal-room/kg/plan.md) and [[Local]].";
    expect(referencedWorkspaceIds(desk)).toEqual(["legal-room", "deal-room"]);
    expect(referencedWorkspaceIds(null)).toEqual([]);
  });
});

describe("the tree", () => {
  const paths = ["README.md", "kg/entities/person/dmitry.md", "kg/entities/company/helm.md",
                 "kg/templates/person.md", "flows/personal.md", ".git/HEAD", "drafts/a.md"];

  it("nests, drops machinery, and puts directories before files", () => {
    const t = treeFrom(paths);
    expect(t.map((n) => n.name)).toEqual(["drafts", "kg", "README.md"]);
    const kg = t.find((n) => n.name === "kg")!;
    expect(kg.children.map((n) => n.name)).toEqual(["entities"]);          // templates/ is gone
    const ent = kg.children[0];
    expect(ent.children.map((n) => n.name)).toEqual(["company", "person"]);
    expect(ent.children[0].children[0]).toMatchObject({ name: "helm.md", path: "kg/entities/company/helm.md", dir: false });
  });

  it("an empty workspace is an empty tree, not a crash", () => {
    expect(treeFrom([])).toEqual([]);
    expect(treeFrom(["flows/only.md"])).toEqual([]);
  });
});

describe("the filter (decision 27.3)", () => {
  const ws: NavWorkspace[] = [
    { key: "desk", slug: undefined, name: "Desk", kind: "desk", readable: true },
    { key: "acme-kg", slug: "acme-kg", name: "Acme", kind: "group", readable: true },
    { key: "legal-room", slug: "legal-room", name: "Legal", kind: "group", readable: false },
  ];
  const trees = {
    desk: ["README.md", "kg/entities/person/brief-writer.md", "drafts/BRIEF.md", "flows/brief.md"],
    "acme-kg": ["kg/brief-2026.md", "kg/other.md"],
    "legal-room": ["kg/secret-brief.md"],
  };

  it("matches names case-insensitively and groups the hits by workspace", () => {
    const r = filterByName("brief", ws, trees);
    expect(r.groups.map((g) => g.key)).toEqual(["desk", "acme-kg"]);
    expect(r.groups[0].paths).toEqual(["drafts/BRIEF.md", "kg/entities/person/brief-writer.md"]);
    expect(r.groups[1].paths).toEqual(["kg/brief-2026.md"]);
  });

  it("never reaches into a workspace the reader cannot open", () => {
    expect(filterByName("brief", ws, trees).groups.some((g) => g.key === "legal-room")).toBe(false);
  });

  it("machinery stays hidden under the filter too", () => {
    expect(filterByName("brief", ws, trees).groups[0].paths).not.toContain("flows/brief.md");
  });

  it("matches the NAME, not the path — typing a folder is not a search for its files", () => {
    expect(filterByName("drafts", ws, trees).groups).toEqual([]);
  });

  it("caps at 50 HITS across the list, and says so", () => {
    const many = { desk: Array.from({ length: 60 }, (_, i) => `kg/note-${String(i).padStart(2, "0")}.md`), "acme-kg": ["kg/note-x.md"] };
    const r = filterByName("note", ws, many);
    expect(r.shown).toBe(MAX_HITS);
    expect(r.truncated).toBe(true);
    expect(r.groups[0].paths).toHaveLength(MAX_HITS);
  });

  it("an empty query is not a search — it is the tree", () => {
    expect(filterByName("   ", ws, trees)).toEqual({ groups: [], shown: 0, truncated: false });
  });

  it("`>` asks for CONTENT and is stripped from what is matched", () => {
    expect(parseQuery("> brief")).toEqual({ text: "brief", content: true });
    expect(parseQuery("brief")).toEqual({ text: "brief", content: false });
    // no content route on this build: the names still answer, so the box is never dead
    expect(filterByName("> brief", ws, trees).groups.map((g) => g.key)).toEqual(["desk", "acme-kg"]);
  });
});

describe("remembered per browser (decision 27.4)", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to HIDDEN when nothing is stored", () => {
    expect(loadNavOpen()).toBe(false);
  });

  it("round-trips the choice", () => {
    saveNavOpen(true);
    expect(localStorage.getItem(NAV_OPEN_KEY)).toBe("1");
    expect(loadNavOpen()).toBe(true);
    saveNavOpen(false);
    expect(loadNavOpen()).toBe(false);
  });

  it("a storage that throws means HIDDEN, never a crash", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
    expect(loadNavOpen()).toBe(false);
    expect(() => saveNavOpen(true)).not.toThrow();
    get.mockRestore(); set.mockRestore();
  });
});

describe("the view slot — a click navigates, it does not collect (decision 28)", () => {
  const seen: unknown[] = [];
  const spy = (e: Event) => seen.push((e as CustomEvent).detail);
  beforeEach(() => { seen.length = 0; window.addEventListener(VIEW_NAVIGATE_EVENT, spy); });
  afterEach(() => window.removeEventListener(VIEW_NAVIGATE_EVENT, spy));

  it("addresses a workspace file, and the desk with NO workspace", () => {
    expect(viewSlotFor("acme-kg", "kg/brief.md")).toEqual({ workspace: "acme-kg", path: "kg/brief.md", label: "brief" });
    expect(viewSlotFor(undefined, "README.md")).toEqual({ workspace: undefined, path: "README.md", label: "README" });
    expect(viewSlotFor("", "a/b.md").workspace).toBeUndefined();
  });

  it("announces the destination", () => {
    navigateView("acme-kg", "kg/brief.md");
    expect(seen).toEqual([{ workspace: "acme-kg", path: "kg/brief.md", label: "brief" }]);
  });

  it("refuses a path that walks out of its mount", () => {
    navigateView(undefined, "../../etc/passwd");
    navigateView(undefined, "");
    expect(seen).toEqual([]);
  });
});

describe("one mechanism: a navigator click and a chip click land in the same history", () => {
  // The navigator was written against a STUB seam while the view slot was still on a branch, and
  // the branch shipped the slot as an in-shell effect with no event. For a while the placeholder
  // was the only definition, and its listener set the document state directly — so a navigator
  // click skipped the back/forward stack and the strip, and the same file reached by two routes
  // landed in two different places. This pins that they are one route.
  it("both routes produce the SAME view slot for the same file", () => {
    // what the navigator announces
    const fromNavigator = viewSlotFor("acme-kg", "kg/brief.md");
    // what a chip/link click resolves to (pageForDocRef, given a resolver answer)
    const fromChip = pageForDocRef({ path: "kg/brief.md", slug: "acme-kg" },
      { path: "kg/brief.md", slug: "acme-kg" })!;
    expect(fromNavigator.path).toBe(fromChip.path);
    expect(fromNavigator.workspace).toBe(fromChip.slug);
    // and therefore the same identity in the strip — which is what "same history" means: one entry,
    // moved, rather than two chips for one document
    expect(artifactKey({ path: fromNavigator.path, slug: fromNavigator.workspace }))
      .toBe(artifactKey({ path: fromChip.path, slug: fromChip.slug }));
  });

  it("a navigator click DEDUPES against a chip click in the strip, it does not add a second entry", () => {
    const slot = viewSlotFor("acme-kg", "kg/brief.md");
    const asArtifact = { path: slot.path, slug: slot.workspace, label: slot.label };
    // chip first, navigator second — one entry, moved to the right edge
    const afterChip = touchHistory([], asArtifact, 1);
    const afterNav = touchHistory(afterChip, asArtifact, 2);
    expect(afterNav).toHaveLength(1);
    expect(afterNav[0].at).toBe(2);
  });

  it("the desk (no workspace) is the same slot either way", () => {
    const nav = viewSlotFor("", "README.md");
    expect(nav.workspace).toBeUndefined();
    expect(artifactKey({ path: nav.path, slug: nav.workspace })).toBe("|README.md");
  });
});

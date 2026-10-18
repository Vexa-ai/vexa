/** docLinks — the ONE resolver behind every doc link format (wikilinks, workspace paths,
 *  relative markdown links). Proves the two bugs that made shared-workspace links dead:
 *  slug-blind wikilink resolution and unresolved `../` relative paths. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { normalizeDocPath, resolveDocRef, docPathExists, entitySlug, invalidateDocLinkCaches } from "../docLinks";

const trees: Record<string, string[]> = {};
let active: { slug: string }[] = [];
let treeReads = 0;
/** Fires AFTER each tree read has captured its answer, carrying the slug that was read ("" = home)
 *  — lets a test land a write between the two passes resolveDocRef makes, which is exactly what an
 *  agent turn does. The slug is load-bearing: the search order now starts at the mandatory `_global`
 *  tier, so a listener that writes on ANY read would land its write inside the FIRST pass and the
 *  re-read it means to prove would never happen. */
let onTreeRead: ((slug: string) => void) | null = null;
vi.mock("../../data/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async (opts?: { slug?: string }) => {
    const answer = trees[opts?.slug ?? ""] ?? [];
    treeReads++;
    onTreeRead?.(opts?.slug ?? "");
    return answer;
  }),
  readActiveSet: vi.fn(async () => ({ subject: "u", active })),
}));

beforeEach(() => {
  invalidateDocLinkCaches();
  for (const k of Object.keys(trees)) delete trees[k];
  active = [];
  treeReads = 0;
  onTreeRead = null;
});

describe("normalizeDocPath", () => {
  it("resolves ../ against the linking doc's directory", () => {
    expect(normalizeDocPath("../entities/project/dna.md", "kg/dashboards/dna.md")).toBe("kg/entities/project/dna.md");
  });
  it("resolves ./ siblings", () => {
    expect(normalizeDocPath("./index.md", "kg/entities/person/x.md")).toBe("kg/entities/person/index.md");
  });
  it("leaves root-relative paths alone (and strips anchors)", () => {
    expect(normalizeDocPath("kg/entities/person/x.md#top", undefined)).toBe("kg/entities/person/x.md");
  });
  it("does not escape above the workspace root", () => {
    expect(normalizeDocPath("../../../../etc/passwd", "kg/a.md")).toBe("etc/passwd");
  });
});

describe("entitySlug", () => {
  it("slugifies titles the way entity files are named", () => {
    expect(entitySlug("James Hollister")).toBe("james-hollister");
    expect(entitySlug("Meeting 96088138284")).toBe("meeting-96088138284");
  });
});

describe("entitySlug — the server's entity id (core/workspaces/shared/entities.py slugify)", () => {
  it("folds accents instead of dropping the letter, and drops apostrophes", () => {
    expect(entitySlug("mühlemann+popp AG")).toBe("muhlemann-popp-ag");
    expect(entitySlug("Keith O'Donnell")).toBe("keith-odonnell");
    expect(entitySlug("Keith O’Donnell")).toBe("keith-odonnell");
    expect(entitySlug("Zürich Café")).toBe("zurich-cafe");
  });
  // The same vectors as core/agent/tests/test_slugify_unicode.py (#1783): a slug the server writes
  // for a Cyrillic, Greek or CJK name is the slug the chip looks for.
  it("transliterates Cyrillic and Greek exactly as the server does", () => {
    expect(entitySlug("Дмитрий Гранкин")).toBe("dmitrii-grankin");
    expect(entitySlug("Щукин Юлия")).toBe("shchukin-iuliia");
    expect(entitySlug("Ёлка Объект")).toBe("elka-obieekt");
    expect(entitySlug("Київ Ґанок")).toBe("kiiv-ganok");
    expect(entitySlug("Ελένη Παπαδοπούλου")).toBe("eleni-papadopoulou");
    expect(entitySlug("Θεσσαλονίκη")).toBe("thessaloniki");
    expect(entitySlug("ООО Ромашка LLC")).toBe("ooo-romashka-llc");
  });
  it("gives a script with no table the server's stable hash slug", () => {
    expect(entitySlug("東京")).toBe("u-68dea76f");
    expect(entitySlug("محمد")).toBe("u-e375c9cf");
    expect(entitySlug("  東京  ")).toBe("u-68dea76f");
    for (const n of ["", "   ", "'", "---", "!!!"]) expect(entitySlug(n)).toBe("");
  });
  it("leaves every Latin name's slug where it was", () => {
    expect(entitySlug("Łódź Ventures")).toBe("odz-ventures");
    expect(entitySlug("İstanbul Office")).toBe("istanbul-office");
    expect(entitySlug("Nguyễn Thị Minh Khai")).toBe("nguyen-thi-minh-khai");
    expect(entitySlug("x".repeat(120))).toBe("x".repeat(80));
  });
  it("resolves a non-ASCII title to the company page the server wrote", async () => {
    trees[""] = ["kg/entities/company/muhlemann-popp-ag.md"];
    const r = await resolveDocRef({ wikilink: "mühlemann+popp AG" }, {});
    expect(r).toEqual({ path: "kg/entities/company/muhlemann-popp-ag.md", slug: undefined, type: "company" });
  });
});

describe("resolveDocRef — wikilinks", () => {
  it("resolves inside the doc's OWN (shared) workspace first — the dead-link bug", async () => {
    trees["dna"] = ["kg/entities/person/james-hollister.md"];
    trees[""] = [];
    const r = await resolveDocRef({ wikilink: "James Hollister" }, { path: "README.md", slug: "dna" });
    expect(r).toEqual({ path: "kg/entities/person/james-hollister.md", slug: "dna", type: "person" });
  });
  it("falls back to the home workspace, then the mounted active set", async () => {
    trees["dna"] = [];
    trees[""] = [];
    trees["other"] = ["kg/entities/company/vexa.md"];
    active = [{ slug: "dna" }, { slug: "other" }];
    const r = await resolveDocRef({ wikilink: "Vexa" }, { slug: "dna" });
    expect(r).toEqual({ path: "kg/entities/company/vexa.md", slug: "other", type: "company" });
  });
  it("returns undefined when no mounted workspace has the entity (renders the muted chip)", async () => {
    expect(await resolveDocRef({ wikilink: "Nobody" }, {})).toBeUndefined();
  });

  /** The defect the founder hit: these trees are cached for a minute, and the agent WRITES an
   *  entity doc during the very turn whose reply names it. Every chip in that reply resolved to
   *  "not found" against a tree read before the write — and a not-found chip did nothing at all
   *  when clicked. A miss must therefore cost one fresh read before it is believed. */
  it("re-reads the trees before declaring a title missing — the doc was written mid-turn", async () => {
    trees[""] = [];
    // write on the HOME read only: it is last in the search order, so the write lands after the
    // first pass has already missed — which is the condition this test exists to prove.
    onTreeRead = (slug) => { if (slug === "") trees[""] = ["kg/entities/company/openvdb-foundation.md"]; };
    const r = await resolveDocRef({ wikilink: "OpenVDB Foundation" }, {});
    expect(r).toEqual({ path: "kg/entities/company/openvdb-foundation.md", slug: undefined, type: "company" });
    expect(treeReads).toBeGreaterThan(1);   // it did not trust the cached miss
  });
  it("resolves organisation entities from the mandatory _global tier even though it is not in /workspace/active", async () => {
    trees["_global"] = ["kg/entities/company/example-bank.md"];
    active = [{ slug: "personal" }];
    trees["personal"] = [];
    const r = await resolveDocRef({ wikilink: "Example Bank" }, {});
    expect(r).toEqual({
      path: "kg/entities/company/example-bank.md",
      slug: "_global",
      type: "company",
    });
  });
});

describe("resolveDocRef — paths", () => {
  it("normalizes relative paths against the doc and keeps its workspace", async () => {
    trees["dna"] = ["kg/entities/project/dna.md"];
    const r = await resolveDocRef({ path: "../entities/project/dna.md" }, { path: "kg/dashboards/dna.md", slug: "dna" });
    expect(r).toEqual({ path: "kg/entities/project/dna.md", slug: "dna" });
  });
  it("tries doc-relative when the root-relative path doesn't exist", async () => {
    trees[""] = ["kg/dashboards/notes.md"];
    const r = await resolveDocRef({ path: "notes.md" }, { path: "kg/dashboards/dna.md" });
    expect(r).toEqual({ path: "kg/dashboards/notes.md", slug: undefined });
  });
  it("still opens a missing path (loud '(not found)' beats a dead click)", async () => {
    const r = await resolveDocRef({ path: "kg/gone.md" }, { slug: "dna" });
    expect(r).toEqual({ path: "kg/gone.md", slug: "dna" });
  });
});

describe("resolveDocRef — a path names its workspace, or opens nothing (founder ruling 2026-10-18)", () => {
  it("a BARE path in a chat (no page around it) resolves to nothing — never the _global README", async () => {
    // the founder's bug: `README.md` in a Narrative chat opened "The organisation › README.md"
    trees["_global"] = ["README.md"];
    trees["narrative"] = ["README.md", "narratives.md"];
    trees[""] = ["README.md"];
    active = [{ slug: "narrative" }];
    expect(await resolveDocRef({ path: "README.md" }, {})).toBeUndefined();
    expect(await resolveDocRef({ path: "narratives.md" }, {})).toBeUndefined();
    expect(await docPathExists("README.md", {})).toBe(false);
  });
  it("a bare path INSIDE a page reads that page's workspace only — no search over _global or the mounts", async () => {
    trees["_global"] = ["kg/notes.md"];
    trees["three"] = ["kg/notes.md"];
    active = [{ slug: "three" }];
    trees["dna"] = [];
    const r = await resolveDocRef({ path: "kg/notes.md" }, { path: "README.md", slug: "dna" });
    expect(r).toEqual({ path: "kg/notes.md", slug: "dna" });       // the page's own room, "(not found)"
    expect(await docPathExists("kg/notes.md", { path: "README.md", slug: "dna" })).toBe(false);
  });
  it("a QUALIFIED path opens the workspace it names", async () => {
    trees["_global"] = ["README.md"];
    trees["narrative"] = ["README.md"];
    active = [{ slug: "narrative" }];
    expect(await resolveDocRef({ path: "/workspaces/narrative/README.md" }, {}))
      .toEqual({ path: "README.md", slug: "narrative" });
    expect(await resolveDocRef({ path: "README.md", slug: "narrative", exact: true }, {}))
      .toEqual({ path: "README.md", slug: "narrative" });
  });
  it("wikilinks (entity names) still search active mounts before the no-slug tree (#1783)", async () => {
    trees[""] = ["kg/entities/person/jane-liu.md"];
    trees["three"] = ["kg/entities/person/jane-liu.md"];
    active = [{ slug: "three" }];
    const r = await resolveDocRef({ wikilink: "Jane Liu" }, {});
    expect(r?.slug).toBe("three");
  });
});

describe("resolveDocRef — worker-visible absolute paths (chat links)", () => {
  it("translates an attached-mount path to {slug, relative}", async () => {
    trees["dna"] = ["kg/entities/project/dna.md"];
    const r = await resolveDocRef(
      { path: "/workspaces/user1/.attached/user1/dna/kg/entities/project/dna.md" }, {});
    expect(r).toEqual({ path: "kg/entities/project/dna.md", slug: "dna" });
  });
  it("translates a home-mount path to its kg/ tail", async () => {
    trees[""] = ["kg/entities/person/x.md"];
    const r = await resolveDocRef({ path: "/workspaces/u/kg/entities/person/x.md" }, {});   // `u` = the subject
    expect(r).toEqual({ path: "kg/entities/person/x.md", slug: undefined });
  });
  it("stays in the attached slug when its tree doesn't have the file — never another room's copy", async () => {
    trees["dna"] = [];
    trees[""] = ["kg/notes.md"];
    const r = await resolveDocRef({ path: "/w/.attached/u/dna/kg/notes.md" }, {});
    expect(r).toEqual({ path: "kg/notes.md", slug: "dna" });
  });
});

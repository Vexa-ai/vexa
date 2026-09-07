/** docLinks — the ONE resolver behind every doc link format (wikilinks, workspace paths,
 *  relative markdown links). Proves the two bugs that made shared-workspace links dead:
 *  slug-blind wikilink resolution and unresolved `../` relative paths. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { normalizeDocPath, resolveDocRef, entitySlug, invalidateDocLinkCaches } from "../docLinks";

const trees: Record<string, string[]> = {};
let active: { slug: string }[] = [];
let treeReads = 0;
/** Fires AFTER each tree read has captured its answer, carrying the slug that was read ("" = home)
 *  — lets a test land a write between the two passes resolveDocRef makes, which is exactly what an
 *  agent turn does. The slug is load-bearing: the search order now starts at the mandatory `_global`
 *  tier, so a listener that writes on ANY read would land its write inside the FIRST pass and the
 *  re-read it means to prove would never happen. */
let onTreeRead: ((slug: string) => void) | null = null;
vi.mock("../../surfaces/workspaceApi", () => ({
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
    expect(entitySlug("James Spadafora")).toBe("james-spadafora");
    expect(entitySlug("Meeting 96088138284")).toBe("meeting-96088138284");
  });
});

describe("resolveDocRef — wikilinks", () => {
  it("resolves inside the doc's OWN (shared) workspace first — the dead-link bug", async () => {
    trees["dna"] = ["kg/entities/person/james-spadafora.md"];
    trees[""] = [];
    const r = await resolveDocRef({ wikilink: "James Spadafora" }, { path: "README.md", slug: "dna" });
    expect(r).toEqual({ path: "kg/entities/person/james-spadafora.md", slug: "dna", type: "person" });
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
    trees["_global"] = ["kg/entities/company/oesterreichische-nationalbank.md"];
    active = [{ slug: "personal" }];
    trees["personal"] = [];
    const r = await resolveDocRef({ wikilink: "Oesterreichische Nationalbank" }, {});
    expect(r).toEqual({
      path: "kg/entities/company/oesterreichische-nationalbank.md",
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

describe("resolveDocRef — active set outranks the legacy no-slug read (ADR-0028)", () => {
  it("finds a path in an ACTIVE mount before the seed-slot (no-slug) tree", async () => {
    // the seed slot holds a DEACTIVATED workspace's tree with the same file — active wins
    trees[""] = ["README.md"];
    trees["three"] = ["README.md"];
    active = [{ slug: "three" }];
    const r = await resolveDocRef({ path: "README.md" }, {});
    expect(r).toEqual({ path: "README.md", slug: "three" });
  });
  it("wikilinks search active mounts before the no-slug tree", async () => {
    trees[""] = ["kg/entities/person/jane-liu.md"];
    trees["three"] = ["kg/entities/person/jane-liu.md"];
    active = [{ slug: "three" }];
    const r = await resolveDocRef({ wikilink: "Jane Liu" }, {});
    expect(r?.slug).toBe("three");
  });
  it("still falls back to the no-slug tree when no active mount has the file", async () => {
    trees[""] = ["kg/notes.md"];
    active = [{ slug: "three" }];
    trees["three"] = [];
    const r = await resolveDocRef({ path: "kg/notes.md" }, {});
    expect(r).toEqual({ path: "kg/notes.md", slug: undefined });
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
    const r = await resolveDocRef({ path: "/workspaces/user1/kg/entities/person/x.md" }, {});
    expect(r).toEqual({ path: "kg/entities/person/x.md", slug: undefined });
  });
  it("falls back to home when the attached slug's tree doesn't have the file", async () => {
    trees["dna"] = [];
    trees[""] = ["kg/notes.md"];
    const r = await resolveDocRef({ path: "/w/.attached/u/dna/kg/notes.md" }, {});
    expect(r).toEqual({ path: "kg/notes.md", slug: undefined });
  });
});

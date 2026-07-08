/** docLinks — the ONE resolver behind every doc link format (wikilinks, workspace paths,
 *  relative markdown links). Proves the two bugs that made shared-workspace links dead:
 *  slug-blind wikilink resolution and unresolved `../` relative paths. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { normalizeDocPath, resolveDocRef, entitySlug, invalidateDocLinkCaches } from "../docLinks";

const trees: Record<string, string[]> = {};
let active: { slug: string }[] = [];
vi.mock("../../surfaces/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async (opts?: { slug?: string }) => trees[opts?.slug ?? ""] ?? []),
  readActiveSet: vi.fn(async () => ({ subject: "u", active })),
}));

beforeEach(() => {
  invalidateDocLinkCaches();
  for (const k of Object.keys(trees)) delete trees[k];
  active = [];
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

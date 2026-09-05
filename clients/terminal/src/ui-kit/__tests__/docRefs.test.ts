/** docRefs — the TRANSFORM layer: which spellings in a reply become interactive, and which are
 *  left exactly as written.
 *
 *  The founder asked the agent to "reference workspace with its readme". The reply named the
 *  workspace in bold and its README as inline code, and neither was clickable — "no reference, and
 *  when reference it's not interactive", then "workspace reference must be a link to its readme."
 *  Three recognitions answer that, and each has a matching way to go wrong:
 *    - an absolute mount path must chip (and must NOT chip inside a fence);
 *    - a bare relative path must chip only when it RESOLVES (so `package.json` stays code);
 *    - a workspace name must chip only against the KNOWN set (so prose stays prose).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { transformDocRefs, DOC_PATH_IN_CODE } from "../MdxDoc";
import { docPathExists, invalidateDocLinkCaches, primeKnownWorkspaces, lookupWorkspace, resolveDocRef } from "../docLinks";

const trees: Record<string, string[]> = {};
let active: { slug: string; path: string; primary?: boolean }[] = [];
// What the reader BELONGS to, which is not what this chat has in focus. A tree read for a workspace
// that is a membership but not a mount answers empty here, exactly as the server's 403 does.
let memberships: { workspace_id: string; role: string }[] = [];
let subject = "57";
vi.mock("../../surfaces/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async (opts?: { slug?: string }) => trees[opts?.slug ?? ""] ?? []),
  readActiveSet: vi.fn(async () => ({ subject, active })),
  listSharedMemberships: vi.fn(async () => memberships),
}));

beforeEach(async () => {
  invalidateDocLinkCaches();
  for (const k of Object.keys(trees)) delete trees[k];
  subject = "57";
  memberships = [];
  // The server calls the private baseline `seed`, NOT the subject uid (verified live on the rig):
  // only `primary` / the `<root>/<subject>` path identify the workspace this client reads with no
  // slug. A test that names it "57" would pass while the real client sent every home read to a
  // workspace called "seed".
  active = [
    { slug: "seed", path: "/workspaces/57", primary: true },
    { slug: "vexa-team-3183d1", path: "/workspaces/vexa-team-3183d1" },
  ];
  await primeKnownWorkspaces();
});

describe("the founder's path: /workspaces/<slug>/README.md", () => {
  it("resolves to {slug, relative} — the shape only kg/ tails used to reach", async () => {
    trees["vexa-team-3183d1"] = ["README.md", "kg/entities/company/acme.md"];
    expect(await resolveDocRef({ path: "/workspaces/vexa-team-3183d1/README.md" }, {}))
      .toEqual({ path: "README.md", slug: "vexa-team-3183d1" });
  });

  it("maps the subject's OWN mount to the home workspace (no slug)", async () => {
    trees[""] = ["README.md"];
    expect(await resolveDocRef({ path: "/workspaces/57/README.md" }, {}))
      .toEqual({ path: "README.md", slug: undefined });
  });

  it("still opens when nothing has it — the honest empty state, never a dead click", async () => {
    expect(await resolveDocRef({ path: "/workspaces/vexa-team-3183d1/never-written.md" }, {}))
      .toEqual({ path: "never-written.md", slug: "vexa-team-3183d1" });
  });

  it("becomes a chip from inline code AND from prose", () => {
    expect(transformDocRefs("see `/workspaces/vexa-team-3183d1/README.md` now"))
      .toContain('<DocPath path="/workspaces/vexa-team-3183d1/README.md" />');
    expect(transformDocRefs("it lives at /workspaces/vexa-team-3183d1/README.md today"))
      .toContain('<DocPath path="/workspaces/vexa-team-3183d1/README.md" />');
  });

  it("leaves a path that is ALREADY a markdown link alone", () => {
    const src = "[the readme](/workspaces/vexa-team-3183d1/README.md)";
    expect(transformDocRefs(src)).toBe(src);
  });
});

describe("bare relative paths in inline code", () => {
  it("recognizes a `.md` path — the spelling CLAUDE.md tells the agent to use", () => {
    expect(DOC_PATH_IN_CODE.test("kg/entities/company/acme.md")).toBe(true);
    expect(DOC_PATH_IN_CODE.test("README.md")).toBe(true);
    expect(transformDocRefs("open `kg/entities/company/acme.md`"))
      .toContain('<DocPath path="kg/entities/company/acme.md" />');
  });

  it("does NOT recognize inline code that is not a doc path (no false chips)", () => {
    for (const code of ["package.json", "npm run dev", "docker compose up", "meetings_failed", "3183d1"])
      expect(DOC_PATH_IN_CODE.test(code)).toBe(false);
    expect(transformDocRefs("run `npm run dev`")).toBe("run `npm run dev`");
  });

  it("goes live only when the path RESOLVES — a miss stays plain code", async () => {
    trees["vexa-team-3183d1"] = ["kg/entities/company/acme.md"];
    expect(await docPathExists("kg/entities/company/acme.md", {})).toBe(true);
    expect(await docPathExists("kg/entities/company/ghost.md", {})).toBe(false);
  });

  it("resolves a path relative to the doc that names it", async () => {
    trees["vexa-team-3183d1"] = ["kg/entities/person/jane.md"];
    expect(await docPathExists("person/jane.md", { path: "kg/entities/index.md", slug: "vexa-team-3183d1" })).toBe(true);
  });
});

describe("workspace names", () => {
  it("chips a KNOWN slug in bold — the founder's live example, verbatim", () => {
    const out = transformDocRefs("you already have a shared team workspace mounted — **vexa-team-3183d1** and it is ready");
    expect(out).toContain('<WorkspaceRef token="vexa-team-3183d1" />');
    expect(out).not.toContain("**vexa-team-3183d1**");
  });

  it("chips it in inline code and in bare prose too", () => {
    expect(transformDocRefs("the `vexa-team-3183d1` workspace")).toContain('<WorkspaceRef token="vexa-team-3183d1" />');
    expect(transformDocRefs("I wrote it into vexa-team-3183d1 for you")).toContain('<WorkspaceRef token="vexa-team-3183d1" />');
  });

  it("opens the workspace README — the whole point of the chip", () => {
    expect(lookupWorkspace("vexa-team-3183d1")).toEqual({ slug: "vexa-team-3183d1", label: "vexa-team-3183d1" });
    expect(lookupWorkspace("personal")).toEqual({ slug: undefined, label: "personal" });
    expect(lookupWorkspace("seed")).toEqual({ slug: undefined, label: "seed" });   // the private baseline
  });

  it("matches ONLY the known set — never a slug-shaped word", () => {
    expect(lookupWorkspace("some-other-team-ab12cd")).toBeUndefined();
    expect(transformDocRefs("this is a well-known problem")).toBe("this is a well-known problem");
  });

  it("leaves ordinary English alone: an undistinctive token needs bold or backticks", () => {
    // `personal` IS a known token, but as a bare word it is prose — chipping it would be the
    // false-positive twin of the dead chip this all exists to remove.
    expect(transformDocRefs("your personal notes are safe")).toBe("your personal notes are safe");
    expect(transformDocRefs("in **personal**")).toContain('<WorkspaceRef token="personal" />');
  });

  it("emits nothing while the known set is cold — no guessing", async () => {
    active = [];
    invalidateDocLinkCaches();
    await primeKnownWorkspaces();
    expect(transformDocRefs("**vexa-team-3183d1**")).toBe("**vexa-team-3183d1**");
  });
});

describe("fenced code blocks are never touched", () => {
  it("leaves a path, a workspace name and a wikilink inside a fence exactly as written", () => {
    const fence = "```sh\ncat /workspaces/vexa-team-3183d1/README.md\ncd vexa-team-3183d1\n# [[Acme]] `kg/x.md`\n```";
    expect(transformDocRefs(fence)).toBe(fence);
  });

  it("still transforms the prose AROUND a fence", () => {
    const out = transformDocRefs("before **vexa-team-3183d1**\n\n```\n/workspaces/vexa-team-3183d1/README.md\n```\n\nafter `README.md`");
    expect(out).toContain('<WorkspaceRef token="vexa-team-3183d1" />');
    expect(out).toContain("```\n/workspaces/vexa-team-3183d1/README.md\n```");
    expect(out).toContain('<DocPath path="README.md" />');
  });
});

/** THE 2026-09-05 DEFECT — "workspace links can open wrong workspaces — we have unique workspace
 *  but it does not work" (founder).
 *
 *  The reader is a member of two team workspaces and has ONE of them in focus. `README.md` is a
 *  filename every workspace on earth has. So a link naming the unfocused one found nothing in its
 *  own (unmounted, therefore unreadable) tree, fell through to the search across the ACTIVE mounts,
 *  and opened the first README it met — a link to one room opening another, silently, and looking
 *  for all the world like it had worked.
 *
 *  Two rules answer it, and they are the same rule at two altitudes: a NAME resolves against
 *  everything the reader can REACH (membership is a permission; focus is a preference), and a NAMED
 *  workspace is then answered by that workspace alone.
 */
describe("a workspace link opens THAT workspace — never a neighbour", () => {
  beforeEach(async () => {
    // in focus: the desk + 3183d1. A member of, but parked: a2a023.
    memberships = [
      { workspace_id: "vexa-team-3183d1", role: "owner" },
      { workspace_id: "vexa-team-a2a023", role: "contributor" },
    ];
    trees[""] = ["README.md"];               // every workspace has one — that is the whole trap
    trees["_global"] = ["README.md"];
    trees["vexa-team-3183d1"] = ["README.md"];
    // trees["vexa-team-a2a023"] deliberately absent: parked ⇒ the read comes back empty
    invalidateDocLinkCaches();
    await primeKnownWorkspaces();
  });

  it("names a workspace the reader BELONGS to but has not focused — focus is not the permission", () => {
    expect(lookupWorkspace("vexa-team-a2a023")).toEqual({ slug: "vexa-team-a2a023", label: "vexa-team-a2a023" });
    expect(lookupWorkspace("vexa-team-3183d1")).toEqual({ slug: "vexa-team-3183d1", label: "vexa-team-3183d1" });
  });

  it("THE BUG: the parked workspace's README resolves to ITS OWN room, not to the first mount holding one", async () => {
    expect(await resolveDocRef({ path: "/workspaces/vexa-team-a2a023/README.md" }, {}))
      .toEqual({ path: "README.md", slug: "vexa-team-a2a023" });
  });

  it("…and not even when the reading doc lives in the other room (meta must not win over a name)", async () => {
    expect(await resolveDocRef({ path: "/workspaces/vexa-team-a2a023/README.md" },
      { path: "kg/entities/company/acme.md", slug: "vexa-team-3183d1" }))
      .toEqual({ path: "README.md", slug: "vexa-team-a2a023" });
  });

  it("the chip's ref is EXACT: it opens the room it names from anywhere, resolved or not", async () => {
    expect(await resolveDocRef({ path: "README.md", slug: "vexa-team-a2a023", exact: true },
      { path: "kg/entities/company/acme.md", slug: "vexa-team-3183d1" }))
      .toEqual({ path: "README.md", slug: "vexa-team-a2a023" });
    // the same chip pointed at the desk still means the desk, not "no workspace given"
    expect(await resolveDocRef({ path: "README.md", slug: undefined, exact: true }, { slug: "vexa-team-3183d1" }))
      .toEqual({ path: "README.md", slug: undefined });
  });

  it("EXACT beats PREFIX — two slugs sharing a stem stay two rooms", async () => {
    expect(lookupWorkspace("vexa-team")).toBeUndefined();
    expect(lookupWorkspace("vexa-team-3183d")).toBeUndefined();
    expect(lookupWorkspace("vexa-team-a2a023x")).toBeUndefined();
    expect(await resolveDocRef({ path: "/workspaces/vexa-team-3183d1/README.md" }, {}))
      .toEqual({ path: "README.md", slug: "vexa-team-3183d1" });
    expect(await resolveDocRef({ path: "/workspaces/vexa-team-a2a023/README.md" }, {}))
      .toEqual({ path: "README.md", slug: "vexa-team-a2a023" });
  });

  it("an UNKNOWN slug chips NOTHING — the no-false-chips rule", () => {
    expect(lookupWorkspace("vexa-team-ffffff")).toBeUndefined();
    expect(transformDocRefs("**vexa-team-ffffff**")).toBe("**vexa-team-ffffff**");
  });

  it("a slug we cannot PLACE still searches — the exact rule is about rooms the reader HAS", async () => {
    // Deliberate, and `docLinks.test.ts` owns the older half of it ("falls back to home when the
    // attached slug's tree doesn't have the file"). An unreachable slug names no room we could open
    // even in principle, so a copy we can actually serve beats a dead end. The founder's defect was
    // never this: his workspace was one he BELONGS to, and the fix is that belonging now counts.
    expect(await resolveDocRef({ path: "/workspaces/vexa-team-ffffff/README.md" }, {}))
      .toEqual({ path: "README.md", slug: "_global" });
  });

  it("the PERSONAL alias is keyed on the PRIMARY MOUNT, never on the name `seed`", async () => {
    expect(lookupWorkspace("personal")).toEqual({ slug: undefined, label: "personal" });
    expect(lookupWorkspace("seed")).toEqual({ slug: undefined, label: "seed" });      // = the primary mount's slug
    expect(lookupWorkspace("57")).toEqual({ slug: undefined, label: "57" });          // = the subject uid
    // Move the primary elsewhere and `seed` stops meaning home — the mount decides, not the word.
    active = [
      { slug: "desk-9f", path: "/workspaces/57", primary: true },
      { slug: "seed", path: "/workspaces/seed" },
    ];
    invalidateDocLinkCaches();
    await primeKnownWorkspaces();
    expect(lookupWorkspace("desk-9f")).toEqual({ slug: undefined, label: "desk-9f" });
    expect(lookupWorkspace("seed")).toEqual({ slug: "seed", label: "seed" });
  });

  it("a cross-mount link inside a doc still SEARCHES — the exact rule constrains names, not paths", async () => {
    trees["vexa-team-3183d1"] = ["README.md", "kg/entities/person/jane.md"];
    expect(await resolveDocRef({ path: "kg/entities/person/jane.md" }, { path: "README.md" }))
      .toEqual({ path: "kg/entities/person/jane.md", slug: "vexa-team-3183d1" });
  });
});

describe("wikilinks still transform (no regression)", () => {
  it("rewrites [[Title]] in prose and leaves it inside a fence", () => {
    expect(transformDocRefs("see [[Jane Liu]]")).toContain('<Wikilink title="Jane Liu" />');
    expect(transformDocRefs("```\n[[Jane Liu]]\n```")).toBe("```\n[[Jane Liu]]\n```");
  });
});

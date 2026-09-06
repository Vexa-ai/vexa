/** `flows/` IS CONTENT IN `_global` AND MACHINERY EVERYWHERE ELSE (Vexa-ai/vexa#1626).
 *
 *  Founder, 2026-09-06: *"flows live in global, right?"* They do — `_global/flows/*.md`, one
 *  generated page per flow (#1615), written for the administrator and linked from the company
 *  README's map and from every rule row in `POLICIES.md`. The hide list hid them from every
 *  listing, so they opened by link and by nothing else.
 *
 *  Three claims, and the third is the one that would be missed:
 *    1. in `_global` the flow pages are listed — in the navigator's tree, in the filter, and in the
 *       breadcrumb's folder listing;
 *    2. on a desk and in a group they are still machinery, because there `flows/personal.md` is a
 *       playbook `CLAUDE.md` opens, not a page anybody reads;
 *    3. NOTHING ELSE MOVED. `skills/`, `routines/`, `views/`, `policy/`, `kg/templates/`, dotfiles
 *       and a root `CLAUDE.md` are machinery in `_global` too — the exception is one directory,
 *       named as a directory, not "the company layer shows everything".
 */
import { describe, it, expect } from "vitest";
import {
  COMPANY_CONTENT_DIRS, GLOBAL_SLUG, MACHINERY_DIRS, humanPaths, isMachinery, isMachineryEntry,
  machineryDirs,
} from "../machinery";
import { filterByName, treeFrom, type NavWorkspace } from "../navigatorApi";

const FLOW_PAGE = "flows/post_meeting.md";

// ── 1 · the company layer shows them ─────────────────────────────────────────────────────────

describe("`flows/` in `_global`", () => {
  it("a generated flow page is a page there, and machinery anywhere else", () => {
    expect(isMachinery(FLOW_PAGE, GLOBAL_SLUG)).toBe(false);
    expect(isMachinery(FLOW_PAGE)).toBe(true);                 // no slug = the reader's own desk
    expect(isMachinery(FLOW_PAGE, "acme-kg")).toBe(true);      // a group
  });

  it("the directory itself answers the same way as its contents", () => {
    expect(isMachinery("flows", GLOBAL_SLUG)).toBe(false);
    expect(isMachinery("flows", undefined)).toBe(true);
    expect(isMachineryEntry("", "flows", GLOBAL_SLUG)).toBe(false);
    expect(isMachineryEntry("flows", "post_meeting.md", GLOBAL_SLUG)).toBe(false);
    expect(isMachineryEntry("", "flows")).toBe(true);
    expect(isMachineryEntry("flows", "post_meeting.md")).toBe(true);
  });

  it("the exception is subtracted from the one list, never added to it", () => {
    expect(COMPANY_CONTENT_DIRS).toEqual(["flows"]);
    for (const d of machineryDirs(GLOBAL_SLUG)) expect(MACHINERY_DIRS).toContain(d);
    expect(machineryDirs(GLOBAL_SLUG)).not.toContain("flows");
    expect(machineryDirs(undefined)).toEqual(MACHINERY_DIRS);
  });
});

// ── 2 · nothing else moved ───────────────────────────────────────────────────────────────────

describe("everything else is still machinery in `_global`", () => {
  it("the rest of the liquid layer, the shapes, the dotfiles and the instruction file", () => {
    for (const p of ["skills/x.md", "routines/y.md", "views/z.md", "policy/p.md",
                     "kg/templates/person.md", ".git/HEAD", ".claude/settings.json", "CLAUDE.md"]) {
      expect(isMachinery(p, GLOBAL_SLUG)).toBe(true);
    }
  });

  it("a page a person wrote is a page in both", () => {
    for (const slug of [undefined, GLOBAL_SLUG, "acme-kg"]) {
      expect(isMachinery("POLICIES.md", slug)).toBe(false);
      expect(isMachinery("drafts/CLAUDE.md", slug)).toBe(false);
    }
  });
});

// ── 3 · the three listings that ask ──────────────────────────────────────────────────────────

const PATHS = ["README.md", "POLICIES.md", FLOW_PAGE, "flows/README.md",
               "skills/s.md", "kg/templates/person.md", ".git/HEAD"];

describe("the navigator's tree", () => {
  it("expands `flows/` for `_global`", () => {
    const t = treeFrom(PATHS, GLOBAL_SLUG);
    expect(t.map((n) => n.name)).toEqual(["flows", "POLICIES.md", "README.md"]);
    const flows = t.find((n) => n.name === "flows")!;
    expect(flows.dir).toBe(true);
    expect(flows.children.map((n) => n.path)).toEqual([FLOW_PAGE, "flows/README.md"]);
  });

  it("and does not for a desk or a group", () => {
    expect(treeFrom(PATHS).map((n) => n.name)).toEqual(["POLICIES.md", "README.md"]);
    expect(treeFrom(PATHS, "acme-kg").map((n) => n.name)).toEqual(["POLICIES.md", "README.md"]);
  });

  it("humanPaths keeps the order it was given", () => {
    expect(humanPaths(PATHS, GLOBAL_SLUG)).toEqual(["README.md", "POLICIES.md", FLOW_PAGE, "flows/README.md"]);
    expect(humanPaths(PATHS)).toEqual(["README.md", "POLICIES.md"]);
  });
});

describe("the filter", () => {
  const ws: NavWorkspace[] = [
    { key: "desk", slug: undefined, name: "Desk", kind: "desk" },
    { key: GLOBAL_SLUG, slug: GLOBAL_SLUG, name: "Acme", kind: "global" },
  ];
  const trees = { desk: ["flows/post_meeting.md"], [GLOBAL_SLUG]: [FLOW_PAGE] };

  it("finds a flow page in the company layer and not on the desk", () => {
    const r = filterByName("post_meeting", ws, trees);
    expect(r.groups.map((g) => g.key)).toEqual([GLOBAL_SLUG]);
    expect(r.groups[0].paths).toEqual([FLOW_PAGE]);
  });
});

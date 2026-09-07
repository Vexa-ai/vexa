/** A chip in a chat message must never eat its own click (founder, 2026-09-01, walking a real
 *  meeting chat: "the chips do nothing").
 *
 *  Two failures produced that, and both are silent by construction. The shell's open-entity
 *  listener `return`ed whenever the resolver came back empty — and empty is the COMMON case for a
 *  reply that names entities the same turn just created, because the doc-link tree cache is a
 *  minute old by then. So the click reached a handler that chose to do nothing, and nothing is
 *  exactly what a broken listener looks like too.
 *
 *  These cover the function boundary that decision now lives on: event detail (+ whatever the
 *  resolver managed) → the artifact appended to the panel. */
import { describe, expect, it } from "vitest";
import { pageForDocRef, pageForMeetingRef } from "../roomView";

describe("pageForDocRef — a resolved link", () => {
  it("opens the resolved path in the resolved workspace", () => {
    expect(pageForDocRef({ wikilink: "Academy Software Foundation" },
      { path: "kg/entities/company/academy-software-foundation.md", slug: "dna" }))
      .toEqual({ path: "kg/entities/company/academy-software-foundation.md", slug: "dna", label: "academy-software-foundation" });
  });

  it("keeps the home workspace (no slug) when that is where it resolved", () => {
    expect(pageForDocRef({ path: "/workspaces/58/kg/entities/project/dna-project.md" },
      { path: "kg/entities/project/dna-project.md" }))
      .toEqual({ path: "kg/entities/project/dna-project.md", slug: undefined, label: "dna-project" });
  });
});

describe("pageForDocRef — an UNRESOLVED link still opens", () => {
  it("a wikilink nothing has written lands on its canonical entity path, named by its title", () => {
    expect(pageForDocRef({ wikilink: "ASWF DNA group meeting — 2026-09-01" }, null))
      .toEqual({ path: "kg/entities/aswf-dna-group-meeting-2026-09-01.md", slug: undefined, label: "ASWF DNA group meeting — 2026-09-01" });
  });

  it("a path the resolver could not place opens where it points, relative to the linking doc", () => {
    expect(pageForDocRef({ path: "../entities/person/x.md", docPath: "kg/dashboards/d.md" }, null))
      .toEqual({ path: "kg/entities/person/x.md", slug: undefined, label: "x" });
  });

  it("only a detail naming NOTHING opens nothing", () => {
    expect(pageForDocRef({}, null)).toBeNull();
  });
});

describe("pageForMeetingRef — a meeting ref with no row behind it", () => {
  it("falls back to the meeting's notes page instead of swallowing the click", () => {
    expect(pageForMeetingRef("google_meet/abc-defg-hij"))
      .toEqual({ path: "kg/entities/meeting/abc-defg-hij.md", label: "abc-defg-hij" });
  });

  it("takes a bare native id too", () => {
    expect(pageForMeetingRef("abc-defg-hij").path).toBe("kg/entities/meeting/abc-defg-hij.md");
  });
});

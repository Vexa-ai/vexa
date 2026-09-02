/** F41, AS AMENDED BY PRD DECISION 28 — a file the turn wrote NAVIGATES THE VIEW; it never mints a
 *  tab.
 *
 *  F41 was the founder creating a shared workspace, the agent writing its README, and the panel
 *  staying on `_global/README.md` — the one document the turn had just made was the one thing not
 *  on screen. The first fix appended a tab. Decision 28 is the correction to that fix: *"we do not
 *  want to create new tab for every click, tab is only when tab is specifically requested."*
 *
 *  So the event still brings the document to the front when it asks to (`focus: true`), and now
 *  does NOTHING VISIBLE when it does not — where it used to append a tab "quietly behind the
 *  reader". Seven quiet tabs are not quiet. */
import { describe, expect, it } from "vitest";
import { artifactViewEffect, pageForArtifact } from "../roomView";

const ev = (over: Partial<{ workspace: string; path: string; focus: boolean }> = {}) =>
  ({ workspace: "daily", path: "README.md", focus: true, ...over });

describe("pageForArtifact — the event resolved to a tab", () => {
  it("a workspace-qualified write becomes a tab in that workspace", () => {
    expect(pageForArtifact({ workspace: "daily", path: "README.md" }))
      .toEqual({ path: "README.md", slug: "daily", label: "README" });
  });

  it('an EMPTY workspace means the caller\'s own desk — no slug, not a guess', () => {
    // The record resolved it and said "no slug"; the stream deliberately does not guess which
    // workspace was meant, so "" is an answer rather than a missing value.
    expect(pageForArtifact({ workspace: "", path: "notes/today.md" }))
      .toEqual({ path: "notes/today.md", slug: undefined, label: "today" });
  });

  it("a nested path is named by its file, as every other tab is", () => {
    expect(pageForArtifact({ workspace: "daily", path: "kg/entities/acme.md" })?.label).toBe("acme");
  });

  it("opens no tab at all rather than one pointing out of the mount", () => {
    expect(pageForArtifact({ workspace: "daily", path: "../../etc/passwd" })).toBeNull();
    expect(pageForArtifact({ workspace: "daily", path: "  " })).toBeNull();
    expect(pageForArtifact({})).toBeNull();
  });
});

describe("artifactViewEffect — the view moves, a tab is never minted", () => {
  it("`focus: true` navigates the view to the written file", () => {
    expect(artifactViewEffect(ev({ focus: true }), false))
      .toEqual({ view: { path: "README.md", slug: "daily", label: "README" } });
  });

  it("`focus: false` does NOTHING VISIBLE — this is the decision-28 change", () => {
    // It used to append a tab behind the reader. That is the accumulation being removed: a turn
    // that writes four files must not leave four tabs nobody asked for.
    expect(artifactViewEffect(ev({ focus: false }), false)).toBeNull();
    expect(artifactViewEffect(ev({}), false)).not.toBeNull();      // the fixture asks for focus
  });

  it("a reader who chose their own document during the turn is not interrupted", () => {
    // their attention beats our suggestion — unchanged from F41
    expect(artifactViewEffect(ev({ focus: true }), true)).toBeNull();
  });

  it("an unresolvable write moves nothing", () => {
    expect(artifactViewEffect({ workspace: "daily", path: "../../etc/passwd", focus: true }, false)).toBeNull();
    expect(artifactViewEffect({ focus: true }, false)).toBeNull();
  });
});

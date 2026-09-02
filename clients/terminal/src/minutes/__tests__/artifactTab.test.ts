/** F41 — A FILE THE TURN WROTE BECOMES A TAB. Founder ruling 2026-09-02.
 *
 *  He created a shared workspace, the agent wrote its README, and the right panel stayed on
 *  `_global/README.md`: the one document the turn had just made was the one thing not on screen.
 *
 *  The server emits `{"type":"artifact","workspace":"<slug>","path":"<path>","focus":true}` after a
 *  successful write. Everything below is what the client does with it, at the pure function that
 *  decides — the shell only wires this. */
import { describe, expect, it } from "vitest";
import { artifactTabEffect, pageForArtifact } from "../roomView";
import type { Page } from "../types";

const README: Page = { path: "README.md", slug: "_global", label: "README" };
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

describe("artifactTabEffect — append always, focus conditionally", () => {
  it("appends the written file to the tabs the chat already has", () => {
    const out = artifactTabEffect(ev(), [README], false)!;
    expect(out.pages.map((p) => `${p.slug ?? ""}|${p.path}`)).toEqual(["_global|README.md", "daily|README.md"]);
  });

  it("brings it to the front when the event says so", () => {
    expect(artifactTabEffect(ev({ focus: true }), [README], false)!.focus)
      .toEqual({ path: "README.md", slug: "daily", label: "README" });
  });

  it("appends WITHOUT focusing when the event does not ask for it", () => {
    const out = artifactTabEffect(ev({ focus: false }), [README], false)!;
    expect(out.focus).toBeNull();
    expect(out.pages).toHaveLength(2);
  });

  /** THE ONE THAT MATTERS. Decision 18's rule one level down: a person who has opened a document is
   *  reading it, and the agent's own write must not tidy their desk out from under them. */
  it("NEVER steals a focus the reader chose — the tab still appears, they do not move", () => {
    const out = artifactTabEffect(ev({ focus: true }), [README], true)!;
    expect(out.focus).toBeNull();                       // they stay where they are…
    expect(out.pages).toHaveLength(2);                  // …and the new file is there when they want it
    expect(out.pages[1].slug).toBe("daily");
  });

  it("is idempotent — the same file written twice in a turn is ONE tab", () => {
    const once = artifactTabEffect(ev(), [README], false)!;
    const twice = artifactTabEffect(ev(), once.pages, false)!;
    expect(twice.pages).toHaveLength(2);
    expect(twice.pages).toEqual(once.pages);
    // …and a re-write of a file already open still brings it forward, rather than doing nothing
    expect(twice.focus).toEqual(once.pages[1]);
  });

  it("a write we cannot name honestly changes nothing at all", () => {
    expect(artifactTabEffect({ path: "../secrets.md", focus: true }, [README], false)).toBeNull();
  });
});

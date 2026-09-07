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
import { artifactFromToken, artifactViewEffect, pageForArtifact } from "../roomView";
import { artifactKey, touchHistory } from "../chats";

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

describe("`pin: true` — the write ENTERS the strip, pinned (coordinator, 2026-09-02)", () => {
  const at = (over: Partial<{ workspace: string; path: string; focus: boolean; pin: boolean }> = {}) =>
    ({ workspace: "daily", path: "README.md", ...over });

  it("pin without focus: the page is kept, and NOTHING moves", () => {
    // pinning and focusing are separate asks — a turn may want a page kept without interrupting
    // what the reader is looking at
    const eff = artifactViewEffect(at({ pin: true }), false)!;
    expect(eff.pin).toEqual({ path: "README.md", slug: "daily", label: "README" });
    expect(eff.view).toBeUndefined();
  });

  it("pin AND focus: kept and brought to the front", () => {
    const eff = artifactViewEffect(at({ pin: true, focus: true }), false)!;
    expect(eff.pin).toBeTruthy();
    expect(eff.view).toEqual(eff.pin);
  });

  it("focus without pin: moves the view and keeps nothing — today's behaviour, unchanged", () => {
    const eff = artifactViewEffect(at({ focus: true }), false)!;
    expect(eff.view).toBeTruthy();
    expect(eff.pin).toBeUndefined();
  });

  it("neither: still NOTHING VISIBLE", () => {
    expect(artifactViewEffect(at({}), false)).toBeNull();
    expect(artifactViewEffect(at({ focus: false, pin: false }), false)).toBeNull();
  });

  it("a reader's own focus is still never overridden — but the pin STILL lands", () => {
    // their attention beats our suggestion; keeping a page does not interrupt them, so it is not
    // suppressed by the same rule
    const eff = artifactViewEffect(at({ pin: true, focus: true }), true)!;
    expect(eff.view).toBeUndefined();
    expect(eff.pin).toBeTruthy();
  });

  it("a pinned entry is the SAME kind of thing a scaffold pins — one identity, one strip entry", () => {
    const eff = artifactViewEffect(at({ pin: true }), false)!;
    const a = { path: eff.pin!.path, slug: eff.pin!.slug, label: eff.pin!.label, pinned: true };
    // arriving twice in a turn is one entry, still pinned
    const once = touchHistory([], a, 1);
    const twice = touchHistory(once, a, 2);
    expect(twice).toHaveLength(1);
    expect(twice[0].pinned).toBe(true);
  });
});

describe("a LIVE meeting's transcript, opened by an artifact event", () => {
  it("`meeting:<row id>` resolves to the CANVAS, not a document at a path", () => {
    // The canvas is what streams: it binds to the row id and renders segments as they are
    // captured, with the Live header. A doc page cannot do that — and until this, EVERY artifact
    // event produced a doc page, so the canvas was unreachable from a turn.
    expect(pageForArtifact({ path: "meeting:97" }))
      .toEqual({ kind: "meeting", path: "97", label: "Transcript" });
  });

  it("is the SAME slot the `meeting:transcript` preset token produces", () => {
    // one identity for the transcript however it is reached — a preset-declared tab and a
    // mid-turn artifact event must not become two entries for one meeting
    const fromToken = artifactFromToken("meeting:transcript", { meetingId: "97" })!;
    const fromEvent = pageForArtifact({ path: "meeting:97" })!;
    expect(artifactKey(fromEvent)).toBe(artifactKey(fromToken));
  });

  it("refuses a malformed meeting ref rather than opening an empty canvas", () => {
    expect(pageForArtifact({ path: "meeting:" })).toBeNull();
    expect(pageForArtifact({ path: "meeting:a/b" })).toBeNull();
  });

  it("a path that merely CONTAINS the word meeting is still a document", () => {
    expect(pageForArtifact({ path: "kg/entities/meeting/x.md" }))
      .toEqual({ path: "kg/entities/meeting/x.md", slug: undefined, label: "x" });
  });
});

/** roomView — the two decisions a minutes room makes, tested at their function boundary.
 *
 *  meeting → pages : what EXISTS in the room. TWO shapes, not three — the only distinction that
 *                    changes the layout is whether a transcript exists yet.
 *  ?view=  → focus : which artifact the chat opens with.
 *
 *  The pairing is the point: a link may re-order attention inside a room, never manufacture a page
 *  the meeting does not have, and never leave the panel empty. */
import { describe, expect, it } from "vitest";
import { labelMatches, pagesForPhase, resolveView } from "../../minutes/roomView";

const labels = (pages: { label: string }[]) => pages.map((p) => p.label);

/** THE MEETING'S OWN DOCUMENT ARRIVES; IT IS NEVER SPELLED HERE (Vexa-ai/vexa#1588). Every call
 *  below used to pass two arguments and get `kg/entities/meeting/abc.md` back — a path in this
 *  client's own vocabulary that nothing in `core/flows` writes, which is how a meeting whose report
 *  had been written, mailed and dropped opened on "No page here yet". This is the shape
 *  `drop_to_attendees` actually produces, and it comes from `/api/meeting/note`. */
const NOTE = "kg/entities/meeting/2026-03-02-0000-dna-tsc-2026-03-02.md";

describe("pagesForPhase — two shapes, keyed on whether a transcript exists", () => {
  it("prep has no transcript, so it opens the brief you are walking in with", () => {
    expect(labels(pagesForPhase("prep", "abc", null, NOTE))).toEqual(["Brief", "Personal page"]);
  });

  it("live leads with the transcript, brief behind it", () => {
    expect(labels(pagesForPhase("live", "abc", null, NOTE))).toEqual(["Transcript", "Brief", "Personal page"]);
  });

  it("post leads with the transcript, minutes behind it", () => {
    expect(labels(pagesForPhase("post", "abc", null, NOTE))).toEqual(["Transcript", "Minutes", "Personal page"]);
  });

  it("live and post are the SAME shape — same paths, same order, same focused page", () => {
    const live = pagesForPhase("live", "abc", null, NOTE), post = pagesForPhase("post", "abc", null, NOTE);
    expect(live.map((p) => p.path)).toEqual(post.map((p) => p.path));
    expect(live[0].label).toBe(post[0].label);          // the transcript leads in both
  });

  it("only the meeting doc's NAME moves between live and post — it is one file", () => {
    expect(pagesForPhase("live", "abc", null, NOTE)[1].path).toBe(pagesForPhase("post", "abc", null, NOTE)[1].path);
    expect(pagesForPhase("live", "abc", null, NOTE)[1].label).toBe("Brief");
    expect(pagesForPhase("post", "abc", null, NOTE)[1].label).toBe("Minutes");
  });

  it("prep is the one shape that differs — no transcript in it at all", () => {
    expect(labels(pagesForPhase("prep", "abc", null, NOTE))).not.toContain("Transcript");
    expect(labels(pagesForPhase("prep", "abc", null, NOTE)).join("|"))
      .not.toBe(labels(pagesForPhase("live", "abc", null, NOTE)).join("|"));
  });

  it("the meeting doc is the one the SERVER named — never `<native>.md`", () => {
    expect(pagesForPhase("post", "abc", null, NOTE)[1].path).toBe(NOTE);
    expect(pagesForPhase("post", "abc", null, NOTE).map((p) => p.path))
      .not.toContain("kg/entities/meeting/abc.md");
  });

  it("opens one document fewer when the server names none", () => {
    expect(labels(pagesForPhase("post", "abc", null, null))).toEqual(["Transcript", "Personal page"]);
  });

  it("a `?mock=1` room is the one place both pages are keyed on the native id", () => {
    // The fixture has no server to ask and no row for the canvas to bind to, so `mockPhases.ts`
    // keys its canned markdown on `kg/entities/meeting/<native>{,.transcript}.md` and the shell
    // composes that path for a mock and only for a mock. A real room is told (see the suite above).
    expect(pagesForPhase("post", "mock-post", null, "kg/entities/meeting/mock-post.md").map((p) => p.path)).toEqual([
      "kg/entities/meeting/mock-post.transcript.md",
      "kg/entities/meeting/mock-post.md",
      "README.md",
    ]);
  });

  it("no native id — nothing was captured under this row, so only the personal page", () => {
    expect(labels(pagesForPhase("post", undefined))).toEqual(["Personal page"]);
    expect(labels(pagesForPhase("live", null))).toEqual(["Personal page"]);
  });
});

describe("labelMatches — a link names a page by the label's first word", () => {
  it("finds a plain label and a qualified one alike", () => {
    expect(labelMatches("Transcript", "transcript")).toBe(true);
    expect(labelMatches("Transcript · live", "transcript")).toBe(true);
  });

  it("does not match on a qualifier alone", () => {
    expect(labelMatches("Transcript · live", "live")).toBe(false);
  });

  it("does not match a different page", () => {
    expect(labelMatches("Minutes", "transcript")).toBe(false);
  });
});

describe("resolveView — the link decides what is in front", () => {
  // The meeting's own document, at the path `drop_to_attendees` writes and the server names — a
  // link re-orders attention inside a room, and it never has an opinion about where a file lives.
  const M1_NOTE = "kg/entities/meeting/2026-03-02-0000-m1-sync.md";
  const post = pagesForPhase("post", "m1", null, M1_NOTE);
  const live = pagesForPhase("live", "m1", null, M1_NOTE);
  const prep = pagesForPhase("prep", "m1", null, M1_NOTE);

  it("no spec leaves the phase's default in front", () => {
    expect(resolveView(null, post).focus).toBeNull();
    expect(resolveView("", post).focus).toBeNull();
    expect(resolveView(undefined, post).pages).toEqual(post);
  });

  it("focuses a named page of the selected meeting", () => {
    expect(resolveView("transcript", post).focus?.label).toBe("Transcript");
    expect(resolveView("minutes", post).focus?.label).toBe("Minutes");
    expect(resolveView("brief", prep).focus?.label).toBe("Brief");
  });

  it("`transcript` resolves the same way in a live room as in a held one", () => {
    expect(resolveView("transcript", live).focus?.path).toBe(resolveView("transcript", post).focus?.path);
    expect(resolveView("transcript", live).focus?.label).toBe("Transcript");
  });

  it("`brief` reaches the meeting doc of a RUNNING meeting — the second artifact, not the first", () => {
    expect(resolveView("brief", live).focus?.path).toBe(M1_NOTE);
  });

  it("a name the phase did not produce is ignored — the default stands, the panel never blanks", () => {
    const r = resolveView("minutes", prep);              // prep has no minutes yet
    expect(r.focus).toBeNull();
    expect(r.pages).toEqual(prep);
  });

  it("an unknown word is ignored rather than clearing the panel", () => {
    const r = resolveView("wat", post);
    expect(r.focus).toBeNull();
    expect(r.pages).toEqual(post);
  });

  it("file: adds the page and focuses it", () => {
    const r = resolveView("file:kg/entities/company/acme.md", prep);
    expect(labels(r.pages)).toEqual(["Brief", "Personal page", "acme"]);
    expect(r.focus?.path).toBe("kg/entities/company/acme.md");
  });

  it("file: naming a page already open focuses it instead of duplicating it", () => {
    const r = resolveView("file:README.md", prep);
    expect(r.pages).toHaveLength(prep.length);
    expect(r.focus?.label).toBe("Personal page");
  });

  it("comma-separated, last wins focus — and every file: still lands in artifacts[]", () => {
    const r = resolveView("minutes,file:notes/plan.md,transcript", post);
    expect(labels(r.pages)).toEqual(["Transcript", "Minutes", "Personal page", "plan"]);
    expect(r.focus?.label).toBe("Transcript");
  });

  it("an unresolvable token does not steal focus from an earlier resolved one", () => {
    expect(resolveView("minutes,wat", post).focus?.label).toBe("Minutes");
  });

  it("whitespace and empty segments are tolerated", () => {
    expect(resolveView(" minutes , , ", post).focus?.label).toBe("Minutes");
  });

  it("a link cannot walk out of the mount", () => {
    const r = resolveView("file:../../etc/passwd", prep);
    expect(r.pages).toEqual(prep);
    expect(r.focus).toBeNull();
  });

  it("the phase's page list is never mutated by resolving a link against it", () => {
    const before = [...post];
    resolveView("file:notes/plan.md", post);
    expect(post).toEqual(before);
  });
});

/** roomView — the two decisions a minutes room makes, tested at their function boundary.
 *
 *  meeting → pages : what EXISTS in the room. ONE meeting view (founder-approved design,
 *                    2026-10-11) — header, notes, transcript — in every phase; the notes are in it,
 *                    so there is no separate "Brief"/"Minutes" page and no separate Transcript tab.
 *  ?view=  → focus : which artifact the chat opens with.
 *
 *  The pairing is the point: a link may re-order attention inside a room, never manufacture a page
 *  the meeting does not have, and never leave the panel empty. */
import { describe, expect, it } from "vitest";
import { labelMatches, MEETING_VIEW_WORDS, meetingChatFront, pagesForPhase, resolveView } from "../../minutes/roomView";

const labels = (pages: { label: string }[]) => pages.map((p) => p.label);

const NOTE = "kg/entities/meeting/2026-03-02-0000-dna-tsc-2026-03-02.md";

describe("pagesForPhase — one meeting view, in every phase", () => {
  it("a real meeting is the meeting view and the personal page — before, during and after", () => {
    for (const phase of ["prep", "live", "post"] as const) {
      expect(pagesForPhase(phase, "abc", "41", NOTE)).toEqual([
        { kind: "meeting", path: "41", label: "Meeting", permanent: true },
        { path: "README.md", label: "Personal page" },
      ]);
    }
  });

  it("never needs the notes file to exist to open the meeting", () => {
    expect(pagesForPhase("post", "abc", "41", null)).toEqual(pagesForPhase("post", "abc", "41", NOTE));
  });

  it("no page is called Brief, Minutes or Transcript", () => {
    for (const phase of ["prep", "live", "post"] as const) {
      expect(labels(pagesForPhase(phase, "abc", "41", NOTE)).join("|")).not.toMatch(/Brief|Minutes|Transcript/);
    }
  });

  it("a `?mock=1` room (no row) keeps its canned pages, keyed on the native id, named Notes", () => {
    expect(pagesForPhase("post", "mock-post", null, "kg/entities/meeting/mock-post.md").map((p) => [p.path, p.label])).toEqual([
      ["kg/entities/meeting/mock-post.transcript.md", "Transcript"],
      ["kg/entities/meeting/mock-post.md", "Notes"],
      ["README.md", "Personal page"],
    ]);
    expect(labels(pagesForPhase("prep", "mock-prep", null, "kg/entities/meeting/mock-prep.md"))).toEqual(["Notes", "Personal page"]);
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
    expect(labelMatches("Notes", "transcript")).toBe(false);
  });
});

describe("resolveView — the link decides what is in front", () => {
  const post = pagesForPhase("post", "m1", "7", null);
  const live = pagesForPhase("live", "m1", "7", null);
  const prep = pagesForPhase("prep", "m1", "7", null);

  it("no spec leaves the phase's default in front", () => {
    expect(resolveView(null, post).focus).toBeNull();
    expect(resolveView("", post).focus).toBeNull();
    expect(resolveView(undefined, post).pages).toEqual(post);
  });

  it("OLD LINKS KEEP WORKING: view=brief, view=minutes and view=transcript all open the meeting view", () => {
    for (const word of ["brief", "minutes", "transcript", "notes", "meeting", "Minutes"]) {
      for (const room of [prep, live, post]) {
        expect(resolveView(word, room).focus).toMatchObject({ kind: "meeting", path: "7", label: "Meeting" });
      }
    }
    expect(MEETING_VIEW_WORDS).toEqual(expect.arrayContaining(["brief", "minutes", "transcript"]));
  });

  it("an unknown word is ignored rather than clearing the panel", () => {
    const r = resolveView("wat", post);
    expect(r.focus).toBeNull();
    expect(r.pages).toEqual(post);
  });

  it("a meeting word in a room with no meeting is ignored — the default stands, the panel never blanks", () => {
    const desk = pagesForPhase("post", null);
    expect(resolveView("minutes", desk).focus).toBeNull();
  });

  it("a mock room's notes answer to the old names too", () => {
    const mock = pagesForPhase("post", "mock-post", null, "kg/entities/meeting/mock-post.md");
    expect(resolveView("minutes", mock).focus?.label).toBe("Notes");
    expect(resolveView("brief", mock).focus?.label).toBe("Notes");
    expect(resolveView("transcript", mock).focus?.label).toBe("Transcript");
  });

  it("file: adds the page and focuses it", () => {
    const r = resolveView("file:kg/entities/company/acme.md", prep);
    expect(labels(r.pages)).toEqual(["Meeting", "Personal page", "acme"]);
    expect(r.focus?.path).toBe("kg/entities/company/acme.md");
  });

  it("file: naming a page already open focuses it instead of duplicating it", () => {
    const r = resolveView("file:README.md", prep);
    expect(r.pages).toHaveLength(prep.length);
    expect(r.focus?.label).toBe("Personal page");
  });

  it("file: never lands on the meeting view, however numerically alike the path looks", () => {
    const r = resolveView("file:7", post);
    expect(r.focus).toMatchObject({ path: "7", label: "7" });
    expect(r.focus?.kind).toBeUndefined();
  });

  it("comma-separated, last wins focus — and every file: still lands in artifacts[]", () => {
    const r = resolveView("minutes,file:notes/plan.md,transcript", post);
    expect(labels(r.pages)).toEqual(["Meeting", "Personal page", "plan"]);
    expect(r.focus?.label).toBe("Meeting");
  });

  it("an unresolvable token does not steal focus from an earlier resolved one", () => {
    expect(resolveView("minutes,wat", post).focus?.label).toBe("Meeting");
  });

  it("whitespace and empty segments are tolerated", () => {
    expect(resolveView(" minutes , , ", post).focus?.label).toBe("Meeting");
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

describe("meetingChatFront — a meeting chat opens on its meeting", () => {
  const desk = { path: "README.md", label: "Desk", desk: true };
  const view = { kind: "meeting" as const, path: "7", label: "Meeting", permanent: true };
  const plan = { path: "drafts/plan.md", label: "plan" };
  const list = [desk, view, plan];
  it("the view, when the chat would otherwise open on the desk or on nothing", () => {
    expect(meetingChatFront(list, desk)).toBe(view);
    expect(meetingChatFront(list, { path: "README.md", label: "Desk" })).toBe(view);
    expect(meetingChatFront(list, undefined)).toBe(view);
  });
  it("the reader's own page, when that is where they were", () => {
    expect(meetingChatFront(list, plan)).toBe(plan);
  });
  it("a chat with no meeting view keeps what it picked", () => {
    expect(meetingChatFront([desk, plan], desk)).toBe(desk);
  });
});

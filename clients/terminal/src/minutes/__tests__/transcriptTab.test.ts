/** The transcript tab is the meeting CANVAS, not a file — tested where that becomes true.
 *
 *  `kg/entities/meeting/<native>.transcript.md` was a dead pointer: nothing writes it, and the only
 *  thing that ever made it look alive was the mock's 2.5s re-read. The room now hands the panel a
 *  `kind: "meeting"` tab carrying the meeting ROW ID, and the panel renders the registered meeting
 *  surface for it.
 *
 *  Three things have to hold and each has a way of failing quietly: the split between a real meeting
 *  and a `?mock=1` fixture, the tab identity (a row id and a workspace path must never key alike),
 *  and the migration — every chat already on disk has artifacts with no `kind` at all. */
import { describe, expect, it, beforeEach } from "vitest";
import { pagesForPhase, resolveView } from "../roomView";
import { CHATS_KEY, artifactKey, loadChats, type Artifact } from "../chats";

const labels = (ps: { label: string }[]) => ps.map((p) => p.label);

/** The meeting's own document, at the path `drop_to_attendees` writes and `/api/meeting/note`
 *  names. It ARRIVES — this client cannot spell it (Vexa-ai/vexa#1588). */
const NOTE = "kg/entities/meeting/2026-03-02-0000-dna-tsc.md";

describe("pagesForPhase — a real meeting is the meeting view, bound to its row", () => {
  it("binds the view to the ROW ID, not to a workspace path", () => {
    const [transcript] = pagesForPhase("post", "abc-native", "1234");
    expect(transcript.label).toBe("Meeting");
    expect(transcript.kind).toBe("meeting");
    expect(transcript.path).toBe("1234");
    expect(transcript.slug).toBeUndefined();
  });

  it("does the same while the meeting is LIVE — that is the point of the canvas", () => {
    expect(pagesForPhase("live", "abc-native", "1234")[0].kind).toBe("meeting");
  });

  it("the notes are IN the view — no separate document tab, and the personal page stays a document", () => {
    const post = pagesForPhase("post", "abc-native", "1234", NOTE);
    expect(labels(post)).toEqual(["Meeting", "Personal page"]);
    expect(post.map((p) => p.path)).not.toContain(NOTE);
    expect(post[1].kind).toBeUndefined();
  });

  it("a meeting with no row behind it (`?mock=1`) keeps the canned markdown page", () => {
    const [transcript] = pagesForPhase("post", "mock-post", null, "kg/entities/meeting/mock-post.md");
    expect(transcript.kind).toBeUndefined();
    expect(transcript.path).toBe("kg/entities/meeting/mock-post.transcript.md");
  });

  it("prep opens the same view — nothing said yet is something the view says, not a different room", () => {
    expect(labels(pagesForPhase("prep", "abc-native", "1234", NOTE))).toEqual(["Meeting", "Personal page"]);
  });

  it("a row with no native id (an imported call) still opens the meeting view", () => {
    expect(labels(pagesForPhase("live", null, "1234"))).toEqual(["Meeting", "Personal page"]);
    expect(labels(pagesForPhase("live", null, null))).toEqual(["Personal page"]);
  });
});

describe("artifactKey — a row id and a workspace path never key alike", () => {
  it("a meeting tab and a document at the same string are different tabs", () => {
    expect(artifactKey({ kind: "meeting", path: "1234" })).not.toBe(artifactKey({ path: "1234" }));
  });

  it("a DOC key is byte-for-byte what it always was — persisted `focus` values keep resolving", () => {
    expect(artifactKey({ path: "README.md" })).toBe("|README.md");
    expect(artifactKey({ path: "README.md", slug: "_global" })).toBe("_global|README.md");
    expect(artifactKey({ kind: "doc", path: "README.md" })).toBe("|README.md");
  });

  it("two meetings are two tabs", () => {
    expect(artifactKey({ kind: "meeting", path: "1" })).not.toBe(artifactKey({ kind: "meeting", path: "2" }));
  });
});

describe("stored chats migrate for free", () => {
  beforeEach(() => localStorage.clear());

  //  is not decoration: the load path PRUNES a chat with no human turn and no
  // scaffold behind it (F35, 2026-09-02), so an untouched fixture would not survive to be read and
  // the assertion below would pass for the wrong reason. A chat whose tabs are worth migrating is
  // by definition one somebody worked in.
  const stored = (artifacts: unknown[]) => {
    // PINNED (PRD decision 28): a tab exists only when it was asked for, and `loadChats` collapses
    // unpinned ones. What is under test in this block is `normalise`'s tolerance of the artifact
    // SHAPE — a missing `kind`, a meeting kind, a kind nobody recognises — not whether a tab is
    // retained. Pinning here keeps that subject rather than weakening the assertions.
    const pinned = (artifacts as Record<string, unknown>[]).map((a) => ({ ...a, pinned: true }));
    localStorage.setItem(CHATS_KEY, JSON.stringify([
      { id: "c1", label: "c1", workspaces: ["personal"], artifacts: pinned, touched: true, createdAt: 1, lastActivityAt: 1 },
    ]));
    return loadChats().find((c) => c.id === "c1")?.artifacts ?? [];
  };

  it("an artifact written before `kind` existed loads as a document", () => {
    const [a] = stored([{ path: "README.md", label: "Personal page" }]);
    expect(a.kind).toBeUndefined();
    expect(artifactKey(a)).toBe("|README.md");
  });

  it("a meeting tab survives a reload", () => {
    const [a] = stored([{ kind: "meeting", path: "1234", label: "Transcript" }]);
    expect(a.kind).toBe("meeting");
  });

  it("a kind we do not understand degrades to a document, never to an unrenderable tab", () => {
    const [a] = stored([{ kind: "hologram", path: "README.md", label: "Personal page" }]);
    expect(a.kind).toBeUndefined();
  });
});

describe("resolveView — a `file:` token never lands on the canvas", () => {
  it("a path that looks like a row id still opens as its own document tab", () => {
    const pages = pagesForPhase("post", "abc-native", "1234");
    const r = resolveView("file:1234", pages);
    expect(r.focus?.kind).toBeUndefined();
    expect(r.pages.filter((p) => p.path === "1234")).toHaveLength(2);   // the canvas and the file
  });

  it("`transcript` (an old link) focuses the meeting view, whatever it is made of", () => {
    const r = resolveView("transcript", pagesForPhase("post", "abc-native", "1234"));
    expect(r.focus?.kind).toBe("meeting");
  });
});

/** A shape assertion, so the panel's contract is stated somewhere a reader can find it. */
describe("the tab handed to the panel", () => {
  it("carries everything the canvas needs and nothing it does not", () => {
    const [t] = pagesForPhase("live", "abc-native", "77");
    const a: Artifact = { kind: t.kind, path: t.path, slug: t.slug, label: t.label };
    expect(a).toEqual({ kind: "meeting", path: "77", slug: undefined, label: "Meeting" });
  });
});

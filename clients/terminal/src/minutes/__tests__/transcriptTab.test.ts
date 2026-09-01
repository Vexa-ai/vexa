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

describe("pagesForPhase — a real meeting's transcript is a canvas tab", () => {
  it("binds the transcript to the ROW ID, not to a workspace path", () => {
    const [transcript] = pagesForPhase("post", "abc-native", "1234");
    expect(transcript.label).toBe("Transcript");
    expect(transcript.kind).toBe("meeting");
    expect(transcript.path).toBe("1234");
    expect(transcript.slug).toBeUndefined();
  });

  it("does the same while the meeting is LIVE — that is the point of the canvas", () => {
    expect(pagesForPhase("live", "abc-native", "1234")[0].kind).toBe("meeting");
  });

  it("Brief, Minutes and the personal page stay DOCUMENTS — they are real files", () => {
    const post = pagesForPhase("post", "abc-native", "1234");
    expect(labels(post)).toEqual(["Transcript", "Minutes", "Personal page"]);
    expect(post.slice(1).every((p) => p.kind === undefined)).toBe(true);
    expect(post[1].path).toBe("kg/entities/meeting/abc-native.md");
  });

  it("a meeting with no row behind it (`?mock=1`) keeps the canned markdown page", () => {
    const [transcript] = pagesForPhase("post", "mock-post");
    expect(transcript.kind).toBeUndefined();
    expect(transcript.path).toBe("kg/entities/meeting/mock-post.transcript.md");
  });

  it("prep has no transcript at all, with or without a row", () => {
    expect(labels(pagesForPhase("prep", "abc-native", "1234"))).toEqual(["Brief", "Personal page"]);
  });

  it("nothing captured under the row → still just the personal page", () => {
    expect(labels(pagesForPhase("live", null, "1234"))).toEqual(["Personal page"]);
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

  const stored = (artifacts: unknown[]) => {
    localStorage.setItem(CHATS_KEY, JSON.stringify([
      { id: "c1", label: "c1", workspaces: ["personal"], artifacts, createdAt: 1, lastActivityAt: 1 },
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

  it("`transcript` still focuses the transcript, whatever it is made of", () => {
    const r = resolveView("transcript", pagesForPhase("post", "abc-native", "1234"));
    expect(r.focus?.kind).toBe("meeting");
  });
});

/** A shape assertion, so the panel's contract is stated somewhere a reader can find it. */
describe("the tab handed to the panel", () => {
  it("carries everything the canvas needs and nothing it does not", () => {
    const [t] = pagesForPhase("live", "abc-native", "77");
    const a: Artifact = { kind: t.kind, path: t.path, slug: t.slug, label: t.label };
    expect(a).toEqual({ kind: "meeting", path: "77", slug: undefined, label: "Transcript" });
  });
});

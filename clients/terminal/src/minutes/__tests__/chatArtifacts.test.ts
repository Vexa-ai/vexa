import { describe, expect, it } from "vitest";
import { artifactLine, chatArtifacts, pageTitleOf, writesOf } from "../chatArtifacts";

const T = (h: number, m: number) => new Date(2026, 9, 15, h, m).getTime();

describe("the right panel belongs to the agent on its left", () => {
  it("lists only this chat's artifacts, newest first, one line each", () => {
    const items = chatArtifacts({
      writes: [{ file: "/workspaces/ait/sponsor-plan.md", at: T(10, 12) }],
      visits: [{ kind: "doc", path: "notes/venue.md", slug: "ait", label: "venue.md", at: T(9, 40) }],
      cards: [{ id: "c1", title: "FINOS filings", at: T(10, 30) }],
      meeting: { id: "120", title: "Tatiana", at: T(9, 0) },
    });
    expect(items.map(artifactLine)).toEqual([
      "Updated card FINOS filings · 10:30",
      "Wrote Sponsor plan · 10:12",
      "Opened Venue · 09:40",
      "Meeting with Tatiana · 09:00",
    ]);
  });

  it("an empty chat has an empty list — nothing from the workspace, nothing from another chat", () => {
    expect(chatArtifacts({ writes: [], visits: [], cards: [] })).toEqual([]);
  });

  it("a file written twice is one entry at its latest write, and a written file is not also 'opened'", () => {
    const items = chatArtifacts({
      writes: [{ file: "/w/ait/plan.md", at: T(10, 0) }, { file: "/w/ait/plan.md", at: T(11, 0) }],
      visits: [{ kind: "doc", path: "plan.md", slug: "ait", label: "plan.md", at: T(11, 5) }],
      cards: [],
    });
    expect(items.map(artifactLine)).toEqual(["Wrote Plan · 11:05"]);
  });

  it("desk and home tabs are not artifacts of the chat", () => {
    expect(chatArtifacts({ writes: [], cards: [],
      visits: [{ path: "README.md", label: "README", desk: true, at: 1 }, { path: "x.md", label: "x", home: true, at: 2 }] })).toEqual([]);
  });

  it("titles read as names, not file names", () => {
    expect(pageTitleOf("drafts/2026-10-15_sponsor-plan.md")).toBe("2026 10 15 sponsor plan");
    expect(pageTitleOf("/w/ait/sponsors.md")).toBe("Sponsors");
  });

  it("writes come from the turns the chat already has", () => {
    expect(writesOf([
      { role: "user", at: 1 },
      { role: "agent", at: 5, ops: [{ file: "/w/a.md", wrote: true }, { file: "/w/b.md", wrote: false }] },
    ])).toEqual([{ file: "/w/a.md", at: 5 }]);
  });
});

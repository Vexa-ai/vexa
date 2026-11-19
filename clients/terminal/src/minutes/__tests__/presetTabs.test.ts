/** A preset's `tabs:` / `focus:` tokens → the chat's opening artifacts (PRD decision 18).
 *
 *  The record is what the panel renders, and this is the only thing that turns a LINK into that
 *  record — so what a token means is a contract between a file the founder edits and a panel he
 *  never sees the code of. Pinned here because getting it subtly wrong is silent: a token that
 *  resolves to the wrong path opens a tab that simply never has content, which reads exactly like
 *  an agent that did not write anything.
 */
import { describe, expect, it } from "vitest";
import { artifactFromToken, artifactsFromTokens } from "../roomView";

const MEETING = {
  native: "abc-defg-hij", notePath: "kg/entities/meeting/2026-03-02-show-b-lighting-dailies.md",
  meetingId: "95", phase: "post" as const, mounts: ["_global", "personal"],
};

describe("artifactFromToken", () => {
  it("meeting:note and meeting:transcript are both the meeting view, bound to the ROW id", () => {
    // one view holds the notes and the transcript (2026-10-11); the canvas fetches by row id, and a
    // native here would render another run's transcript
    for (const phase of ["prep", "live", "post"] as const) {
      for (const token of ["meeting:note", "meeting:transcript"]) {
        expect(artifactFromToken(token, { ...MEETING, phase })).toEqual({ kind: "meeting", path: "95", label: "Meeting" });
      }
    }
  });

  it("with no row (a mock), meeting:note is the notes file the SERVER named — never one built from the native", () => {
    const noRow = { ...MEETING, meetingId: null };
    expect(artifactFromToken("meeting:note", noRow)).toEqual({ path: MEETING.notePath, label: "Notes" });
    expect(artifactFromToken("meeting:note", { native: "abc-defg-hij", meetingId: null })).toBeNull();
  });

  it("a meeting token with no meeting opens one document FEWER, never a broken tab", () => {
    expect(artifactFromToken("meeting:note", { mounts: [] })).toBeNull();
    expect(artifactFromToken("meeting:transcript", { mounts: [] })).toBeNull();
  });

  it("workspace-qualifies only against workspaces the chat actually mounts", () => {
    expect(artifactFromToken("_global/PRINCIPLES.md", MEETING))
      .toEqual({ path: "PRINCIPLES.md", slug: "_global", label: "PRINCIPLES" });
    // `personal` is the reader's own desk: no slug, so it resolves in their default mount
    expect(artifactFromToken("personal/README.md", MEETING))
      .toEqual({ path: "README.md", label: "README" });
    // THE ONE THAT MATTERS: `kg` is not a workspace, so this stays a PATH. Treating the first
    // segment as a slug unconditionally would send this to a workspace called "kg" and open a tab
    // that can never load.
    expect(artifactFromToken("kg/entities/meeting/x.md", MEETING))
      .toEqual({ path: "kg/entities/meeting/x.md", label: "x" });
  });

  it("never walks out of the mount", () => {
    expect(artifactFromToken("../../etc/passwd", MEETING)).toBeNull();
    expect(artifactFromToken("_global/../secrets.md", MEETING)).toBeNull();
    expect(artifactFromToken("   ", MEETING)).toBeNull();
  });
});

describe("artifactsFromTokens", () => {
  it("keeps the preset's order — it is the author's reading order — and drops duplicates", () => {
    const out = artifactsFromTokens(
      ["_global/README.md", "meeting:transcript", "_global/README.md", "meeting:note"], MEETING);
    // `meeting:transcript` and `meeting:note` are the same view — it appears once
    expect(out.map((a) => a.label)).toEqual(["README", "Meeting"]);
    expect(out.map((a) => a.path)).toEqual(["README.md", "95"]);
  });

  it("the setup chat's five files resolve to five tabs on the org tier", () => {
    const five = ["_global/README.md", "_global/PRINCIPLES.md", "_global/OBJECTIVES.md",
                  "_global/STRUCTURE.md", "_global/MISSING.md"];
    const out = artifactsFromTokens(five, { mounts: ["_global"] });
    expect(out).toHaveLength(5);
    expect(out.every((a) => a.slug === "_global")).toBe(true);
    // none of them exists when the chat opens — the tab is named by the FILE, and fills in when
    // the conversation writes it
    expect(out[0]).toEqual({ path: "README.md", slug: "_global", label: "README" });
  });

  it("an unresolvable token is dropped, not rendered as an empty tab", () => {
    expect(artifactsFromTokens(["meeting:note", "_global/README.md"], { mounts: ["_global"] }))
      .toEqual([{ path: "README.md", slug: "_global", label: "README" }]);
  });
});

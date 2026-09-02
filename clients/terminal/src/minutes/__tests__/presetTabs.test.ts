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

const MEETING = { native: "abc-defg-hij", meetingId: "95", phase: "post" as const, mounts: ["_global", "personal"] };

describe("artifactFromToken", () => {
  it("meeting:note is the SAME file under the name the reader needs today", () => {
    // one document, two names — Brief before it happened, Minutes after. The path never moves.
    expect(artifactFromToken("meeting:note", { ...MEETING, phase: "prep" }))
      .toEqual({ path: "kg/entities/meeting/abc-defg-hij.md", label: "Brief" });
    expect(artifactFromToken("meeting:note", MEETING))
      .toEqual({ path: "kg/entities/meeting/abc-defg-hij.md", label: "Minutes" });
  });

  it("meeting:transcript binds the CANVAS to the row id, not the native", () => {
    // the canvas fetches by row id; a native here would render another run's transcript
    expect(artifactFromToken("meeting:transcript", MEETING))
      .toEqual({ kind: "meeting", path: "95", label: "Transcript" });
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
    expect(out.map((a) => a.label)).toEqual(["README", "Transcript", "Minutes"]);
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

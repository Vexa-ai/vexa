/** A meeting note's markers are never shown to a person editing it, and survive the edit (#1783). */
import { describe, expect, it } from "vitest";
import { editableNote, hideNoteMarkers, restoreNoteMarkers } from "../noteMarkers";

const NOTE = [
  "<!-- vexa:transcript meeting=119 -->", "",
  "## What this is about", "<!-- meeting:about:start -->", "Planning the meetup.", "<!-- meeting:about:end -->", "",
  "## Decisions", "<!-- meeting:decisions:start -->", "- Venue: Second Home", "- Date: Nov 6", "<!-- meeting:decisions:end -->", "",
  "A line Jane typed herself.", "",
].join("\n");

describe("hideNoteMarkers / restoreNoteMarkers", () => {
  it("the editor's text carries no marker", () => {
    const { text } = hideNoteMarkers(NOTE);
    expect(text).not.toContain("<!--");
    expect(text).toContain("## Decisions\n- Venue: Second Home");
    expect(text).toContain("A line Jane typed herself.");
  });

  it("an untouched round trip gives the note back byte for byte", () => {
    const h = hideNoteMarkers(NOTE);
    expect(restoreNoteMarkers(h.text, h.markers)).toBe(NOTE);
  });

  it("an edit inside a section stays inside its region", () => {
    const h = hideNoteMarkers(NOTE);
    const edited = h.text.replace("- Date: Nov 6", "- Date: Nov 6\n- Ravi books the room");
    const out = restoreNoteMarkers(edited, h.markers);
    expect(out).toContain("- Date: Nov 6\n- Ravi books the room\n<!-- meeting:decisions:end -->");
    expect(out.startsWith("<!-- vexa:transcript meeting=119 -->")).toBe(true);
  });

  it("a deleted section takes its fences with it", () => {
    const h = hideNoteMarkers(NOTE);
    const out = restoreNoteMarkers(h.text.replace(/## What this is about\nPlanning the meetup\.\n\n/, ""), h.markers);
    expect(out).not.toContain("meeting:about");
    expect(out).toContain("<!-- meeting:decisions:start -->");
  });

  it("an example inside a code fence is left as written", () => {
    const md = "## Notes\n```\n<!-- meeting:about:start -->\n```\n";
    expect(hideNoteMarkers(md).text).toBe(md);
  });
});

describe("editableNote — the whole file, as the Files page's editor and the sidebar's show it", () => {
  const FILE = "---\ntype: meeting\nmeeting: 119\ntranscript_cursor: 29\n---\n\n" + NOTE;
  it("shows no frontmatter and no marker; the save gives the file back", () => {
    const e = editableNote(FILE);
    expect(e.text).not.toContain("<!--");
    expect(e.text).not.toContain("transcript_cursor");
    expect(e.restore(e.text)).toBe(FILE);
  });
  it("an ordinary page is edited as it is", () => {
    const page = "---\ntype: person\n---\n# Jane\n<!-- a note to self -->\n";
    const e = editableNote(page);
    expect(e.text).toBe(page);
    expect(e.restore("x")).toBe("x");
  });
});

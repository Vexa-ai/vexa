/** THE MEETING DOC IS ONE PAGE WITH THE TRANSCRIPT IN IT (Vexa-ai/vexa#1598).
 *
 *  Founder, live, 2026-09-06: *"a kind of doc that has live transcript widget in it, so the right on
 *  meeting thing is a doc with the widget"*. Three claims are worth pinning, and each of them has a
 *  plausible wrong answer that would have shipped:
 *
 *    1. a doc that declares the slot renders the ENGINE, bound to the meeting the marker names —
 *       and never the marker as prose. The marker is an HTML comment, and #1590 strips those before
 *       anything else looks at the source, so the obvious implementation loses the widget silently.
 *    2. a doc WITHOUT the marker renders exactly as it did before this existed. This renderer opens
 *       every page in the product; a meeting feature that changes how a person's README paints is a
 *       regression wearing a feature's clothes.
 *    3. the slot is content-addressed by the DOC, so the same doc renders the same widget wherever
 *       it is opened — and a fenced example of the marker stays an example.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { MdxDoc } from "../MdxDoc";
import {
  hasTranscriptSlot, splitTranscriptSlots, transcriptSlotMarker, TRANSCRIPT_WIDGET_KIND,
} from "../transcriptSlot";
import { registry, type TabProps } from "../../contributions";

// The chips resolve against a workspace snapshot; nothing here is about them, so the world is empty.
vi.mock("../../surfaces/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async () => []),
  readActiveSet: vi.fn(async () => ({ subject: "57", active: [] })),
  listSharedMemberships: vi.fn(async () => []),
}));

/** A meeting doc as `shared/meeting_doc.py` writes it: frontmatter with the cursor, hand-written
 *  prose, the widget slot, and a regenerated region around it. */
const DOC = [
  "---",
  "type: meeting",
  "meeting: 147",
  "transcript_cursor: 2026-09-06T12:04:31.000Z",
  "---",
  "",
  "# DNA TSC 2026-03-02",
  "",
  transcriptSlotMarker("147"),
  "",
  "## What this is about",
  "<!-- meeting:about:start -->",
  "The foundation's technical steering committee.",
  "<!-- meeting:about:end -->",
  "",
].join("\n");

afterEach(cleanup);

describe("the marker — one spelling, and fences are literal", () => {
  it("a doc with no marker is one text segment, byte for byte", () => {
    const plain = "# Desk\n\nplain copy\n";
    expect(splitTranscriptSlots(plain)).toEqual([{ kind: "text", text: plain }]);
    expect(hasTranscriptSlot(plain)).toBe(false);
  });

  it("the marker splits the doc and names the meeting", () => {
    const segs = splitTranscriptSlots("before\n\n<!-- vexa:transcript meeting=147 -->\n\nafter");
    expect(segs.map((s) => s.kind)).toEqual(["text", "transcript", "text"]);
    expect(segs[1]).toEqual({ kind: "transcript", meeting: "147" });
    expect((segs[0] as { text: string }).text).toContain("before");
    expect((segs[2] as { text: string }).text).toContain("after");
  });

  it("quoted, spaced and repeated forms all read as the same marker", () => {
    expect(splitTranscriptSlots(`<!--vexa:transcript   meeting="a-1"-->`)[0])
      .toEqual({ kind: "transcript", meeting: "a-1" });
    const two = splitTranscriptSlots(`x ${transcriptSlotMarker("1")} y ${transcriptSlotMarker("2")} z`);
    expect(two.filter((s) => s.kind === "transcript")).toHaveLength(2);
  });

  it("a FENCE quoting the marker is documentation, not a live meeting", () => {
    const src = ["```md", transcriptSlotMarker("147"), "```"].join("\n");
    expect(hasTranscriptSlot(src)).toBe(false);
    expect(splitTranscriptSlots(src)).toEqual([{ kind: "text", text: src }]);
  });

  it("the same split twice gives the same answer — no regex state between reads", () => {
    expect(splitTranscriptSlots(DOC)).toEqual(splitTranscriptSlots(DOC));
  });
});

describe("<MdxDoc> — the renderer the pages panel opens every page with", () => {
  const seen: TabProps[] = [];

  beforeEach(() => {
    seen.length = 0;
    registry.registerTab(TRANSCRIPT_WIDGET_KIND, (p: TabProps) => {
      seen.push(p);
      return <div data-testid="engine">live transcript for {String(p.params.meetingId)}</div>;
    });
  });

  it("renders the transcript widget IN PLACE, bound to the meeting the doc names", async () => {
    const { container, getByTestId } = render(<MdxDoc>{DOC}</MdxDoc>);
    await waitFor(() => expect(container.textContent).toContain("technical steering committee"));
    expect(getByTestId("engine")).toBeTruthy();
    expect(seen.map((p) => p.params.meetingId)).toEqual(["147"]);
    // the doc's own words survive around it — the widget is IN the page, not instead of it
    expect(container.textContent).toContain("DNA TSC 2026-03-02");
    expect(container.textContent).toContain("What this is about");
  });

  it("prints no marker and no region machinery at the reader (#1590 still holds)", async () => {
    const { container } = render(<MdxDoc>{DOC}</MdxDoc>);
    await waitFor(() => expect(container.textContent).toContain("technical steering committee"));
    for (const m of ["<!--", "-->", "vexa:transcript", "meeting:about:start", "transcript_cursor"])
      expect(container.textContent, m).not.toContain(m);
  });

  it("a doc WITHOUT the marker renders plain, and asks for no widget", async () => {
    const { container, queryByTestId } = render(<MdxDoc>{"# Desk\n\nplain copy\n"}</MdxDoc>);
    await waitFor(() => expect(container.textContent).toContain("plain copy"));
    expect(queryByTestId("engine")).toBeNull();
    expect(seen).toHaveLength(0);
  });

  it("prose that fails to compile downgrades ITSELF; the live transcript beside it stays", async () => {
    // an unbalanced brace is an MDX expression error — the segment falls back to plain Markdown
    const broken = ["# Room", "", transcriptSlotMarker("9"), "", "a stray { brace"].join("\n");
    const { container, getByTestId } = render(<MdxDoc>{broken}</MdxDoc>);
    await waitFor(() => expect(getByTestId("engine")).toBeTruthy());
    expect(container.textContent).toContain("live transcript for 9");
  });
});

describe("a build with no meeting surface registered", () => {
  it("says so in one line, and never shows the reader the marker", async () => {
    // wipe the registration the suite above installed — this is the minutes-less build's state
    registry.registerTab(TRANSCRIPT_WIDGET_KIND, undefined as unknown as (p: TabProps) => null);
    const { container } = render(<MdxDoc>{DOC}</MdxDoc>);
    await waitFor(() => expect(container.textContent).toContain("technical steering committee"));
    expect(container.textContent).toContain("not available in this build");
    expect(container.textContent).not.toContain("vexa:transcript");
  });
});

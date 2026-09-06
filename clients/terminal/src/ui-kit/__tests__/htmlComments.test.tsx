/** HTML COMMENTS ARE MACHINERY — and the page view is not where machinery is read.
 *
 *  Founder, walking his own desk README on 2026-09-06: *"not everything is rendered correctly
 *  here"* — every region marker `core/agent/shared/desk_readme.py` writes was on screen as text.
 *  The fixture below is that page's own shape, markers verbatim, because a renderer that hides
 *  `<!-- x -->` and still prints `<!-- desk:now:end -->` at the end of a sentence has fixed
 *  nothing: the ones that bit sit mid-line, span lines, and wrap a human's hand-edited region.
 *
 *  The two claims with a plausible wrong answer are the exceptions, not the rule: a FENCE quoting
 *  a marker is a transcript and must still read, and an UNTERMINATED `<!--` must cost one stray
 *  marker rather than the rest of the document.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { Markdown, stripHtmlComments } from "../Markdown";
import { MdxDoc } from "../MdxDoc";

// The chips resolve against a workspace snapshot; nothing here is about them, so the world is empty.
vi.mock("../../surfaces/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async () => []),
  readActiveSet: vi.fn(async () => ({ subject: "57", active: [] })),
  listSharedMemberships: vi.fn(async () => []),
}));

/** A desk README, as the generator writes it (`_MARKER`, `PINNED_HINT`) and as the founder met it. */
const DESK = [
  "# Desk",
  "",
  "## Pinned",
  "<!-- desk:pinned:start -->",
  "<!-- Yours. Put the links you want at the top of your desk here — nothing below this section " +
    "is hand-edited, and nothing in it is ever regenerated. -->",
  "- [Academy Software Foundation](kg/entities/company/academy-software-foundation.md)",
  "<!-- desk:pinned:end -->",
  "",
  "## Now",
  "<!-- desk:now:start -->",
  "Nothing scheduled. <!-- desk:now:end -->",
  "",
  "## People",
  "<!-- desk:people:start -->",
  "- Dmitry",
  "<!-- desk:people:end -->",
  "",
].join("\n");

/** Every string the founder saw and should not have. `<!--` covers any marker this list forgets. */
const MACHINERY = [
  "<!--", "-->", "desk:pinned:start", "desk:pinned:end", "desk:now:start", "desk:now:end",
  "desk:people:start", "desk:people:end", "Put the links you want",
];

const saysNothingOfMachinery = (text: string) => {
  for (const m of MACHINERY) expect(text, m).not.toContain(m);
};

afterEach(cleanup);

describe("stripHtmlComments — the desk's own markers", () => {
  it("drops every one of them, wherever on the line it sits", () => {
    const out = stripHtmlComments(DESK);
    saysNothingOfMachinery(out);
    // and the page is still the page
    expect(out).toContain("## Pinned");
    expect(out).toContain("Nothing scheduled.");
    expect(out).toContain("[Academy Software Foundation](kg/entities/company/academy-software-foundation.md)");
  });

  it("a comment that spans lines goes whole — the hint wraps, and it is one comment", () => {
    const src = ["before", "<!-- desk:pinned:start", "  still inside", "  and out -->", "after"].join("\n");
    expect(stripHtmlComments(src).replace(/\s+/g, " ").trim()).toBe("before after");
  });

  it("a FENCE is a transcript — a marker quoted inside one still reads", () => {
    const src = ["```md", "<!-- desk:pinned:start -->", "your links here", "```"].join("\n");
    expect(stripHtmlComments(src)).toBe(src);
  });

  it("inline code NAMES a marker, and naming is not machinery", () => {
    const src = "the region ends at `<!-- desk:now:end -->`, which the generator writes";
    expect(stripHtmlComments(src)).toBe(src);
  });

  it("an unterminated `<!--` costs a stray marker, never the rest of the page", () => {
    const src = "keep me\n<!-- never closed\nand keep me too";
    expect(stripHtmlComments(src)).toBe(src);
  });

  it("nothing to drop is the same string back", () => {
    expect(stripHtmlComments("# Desk\n\nplain copy\n")).toBe("# Desk\n\nplain copy\n");
    expect(stripHtmlComments("")).toBe("");
  });
});

describe("the rendered page", () => {
  it("<Markdown> prints no marker and loses no copy", () => {
    const { container } = render(<Markdown>{DESK}</Markdown>);
    saysNothingOfMachinery(container.textContent ?? "");
    expect(container.textContent).toContain("Nothing scheduled.");
    expect(container.textContent).toContain("Academy Software Foundation");
  });

  it("<MdxDoc> — the renderer the pages panel opens the desk with — prints none either", async () => {
    const { container } = render(<MdxDoc>{DESK}</MdxDoc>);
    await waitFor(() => expect(container.textContent).toContain("Nothing scheduled."));
    saysNothingOfMachinery(container.textContent ?? "");
  });
});

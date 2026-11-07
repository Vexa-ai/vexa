/** Every page is a plain page (UI decisions 12): source tails become citation markers listed once at
 *  the bottom, and headings with nothing under them are not drawn. Rendering only. */
import { describe, expect, it } from "vitest";
import { plainPage, sourcesBlock, withoutEmptyHeadings } from "../plainPage";

const PAGE = [
  "# AI Tinkerers Lisbon",
  "Lisbon chapter of AI Tinkerers.",
  "",
  "## What it is",
  "Last event: PostHog Demo Night, 24 Sep 2026. — source: Dmitry's Gmail threads, read 14 Oct 2026",
  "",
  "## Who",
  "Dmitry (City Lead), Valeriia (events) — source: Dmitry's Gmail threads, read 14 Oct 2026",
  "",
  "## Open questions",
  "",
  "## Status",
  "Next meetup in planning — source: the planning call, 14 Oct",
  "",
  "```",
  "code — source: not a tail",
  "```",
].join("\n");

describe("a plain page", () => {
  it("turns each source tail into a marker, numbering a repeated source once", () => {
    const { body, sources } = plainPage(PAGE);
    expect(sources).toEqual(["Dmitry's Gmail threads, read 14 Oct 2026", "the planning call, 14 Oct"]);
    expect(body).not.toContain("— source:" + " Dmitry");
    expect(body.match(/className="vx-cite" title="Dmitry's Gmail threads, read 14 Oct 2026">1</g)).toHaveLength(2);
    expect(body).toContain('title="the planning call, 14 Oct">2</sup>');
    expect(body).toContain("code — source: not a tail");            // code is left as written
    expect(sourcesBlock(sources)).toContain("1. Dmitry's Gmail threads, read 14 Oct 2026");
  });

  it("drops headings with nothing under them, keeps the rest", () => {
    const out = withoutEmptyHeadings(PAGE);
    expect(out).not.toContain("## Open questions");
    expect(out).toContain("## Status");
    expect(withoutEmptyHeadings("## A\n<!-- meeting:a:start -->\n<!-- meeting:a:end -->\n## B\ntext")).toBe("<!-- meeting:a:start -->\n<!-- meeting:a:end -->\n## B\ntext");
    expect(withoutEmptyHeadings("# T\n## Sub\ntext")).toBe("# T\n## Sub\ntext");
  });

  it("folds the page's own Sources section into the one list at the bottom", () => {
    const { body, sources } = plainPage("# P\ntext — source: A\n\n## Sources\n- A\n- B\n");
    expect(body).not.toContain("## Sources");
    expect(sources).toEqual(["A", "B"]);
  });

  it("never lets a source break out of its marker", () => {
    const { body } = plainPage('x — source: a "quoted" {thing} <b>');
    expect(body).toContain('title="a ″quoted″ thing b"');
  });
});

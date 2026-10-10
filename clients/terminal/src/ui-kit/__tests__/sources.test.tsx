/** An agent's sources become a citation list, with each date shown once (guidelines §4.17). */
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SourceList, extractSources, parseSourceItem, readSources } from "..";

const ANSWER = `Here is what changed this week.

**Sources**
- [Example Company platform launch 2026-10-08](https://news.example.com/launch) (2026-10-08)
- [Release notes](https://example.com/releases) — 2026-09-30
- https://docs.example.com/platform

Anything else?`;

describe("the Markdown fallback", () => {
  it("lifts a 'Sources' list out of the prose", () => {
    const { body, sources } = extractSources(ANSWER);
    expect(sources).toHaveLength(3);
    expect(body).not.toContain("news.example.com");
    expect(body).toContain("Anything else?");
  });
  it("the founder's duplicate: a date in the title is not printed again beside it", () => {
    const { sources } = extractSources(ANSWER);
    const html = renderToStaticMarkup(<SourceList sources={sources} />);
    expect(html.match(/2026-10-08/g)).toHaveLength(1);   // only inside the title
    expect(html).toContain('dateTime="2026-09-30"');       // a date NOT in the title shows once
  });
  it("a bare URL becomes a source titled by its host and path", () => {
    expect(parseSourceItem("https://docs.example.com/platform")).toMatchObject({ url: "https://docs.example.com/platform", title: "docs.example.com/platform" });
  });
  it("a list with an item that is not a link stays prose", () => {
    const md = "Sources:\n- [A](https://a.example.com)\n- just a thought";
    expect(extractSources(md)).toEqual({ body: md, sources: [] });
  });
  it("no label, no lift", () => {
    const md = "- [A](https://a.example.com)";
    expect(extractSources(md).sources).toEqual([]);
  });
});

describe("the structured stream field", () => {
  it("keeps only http(s) sources, with a title", () => {
    expect(readSources([{ title: "A", url: "https://a.example.com", date: "2026-10-08" }, { url: "javascript:alert(1)" }, "x", { url: "http://b.example.com" }]))
      .toEqual([{ title: "A", url: "https://a.example.com", date: "2026-10-08" }, { title: "b.example.com", url: "http://b.example.com" }]);
  });
  it("anything that is not a list is no sources", () => {
    expect(readSources({ url: "https://a.example.com" })).toEqual([]);
  });
});

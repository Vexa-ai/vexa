/** MDX-fallback parity — proves the two halves of the "silent downgrade" fix:
 *  1. Markdown.tsx (where docs land when MDX compile fails) parses <Card>/<CardGroup>
 *     markup instead of printing it as literal tag soup (observed live on
 *     kg/entities/company/oesterreichische-nationalbank.md);
 *  2. MdxDoc escapes unknown raw angle-bracket text so common agent-written prose
 *     (`<meeting_id>`, `a<b`) stops aborting the MDX compile in the first place. */
import { describe, it, expect } from "vitest";
import { parseCardBlock } from "../Markdown";
import { escapeUnknownTags } from "../MdxDoc";

describe("parseCardBlock — <CardGroup>/<Card> in the plain-Markdown fallback", () => {
  it("parses a CardGroup with cols and titled/linked cards", () => {
    const src = [
      '<CardGroup cols={2}>',
      '  <Card title="Profile" icon="building" href="kg/entities/company/oenb.md">',
      '    Central bank of Austria.',
      '  </Card>',
      '  <Card title="Site" icon="web" href="https://oenb.at" />',
      '</CardGroup>',
    ].join("\n");
    const b = parseCardBlock(src);
    expect(b.grouped).toBe(true);
    expect(b.cols).toBe(2);
    expect(b.cards).toEqual([
      { title: "Profile", icon: "building", href: "kg/entities/company/oenb.md", body: "Central bank of Austria." },
      { title: "Site", icon: "web", href: "https://oenb.at", body: "" },
    ]);
  });
  it('accepts cols="3" string form and defaults to 2 when absent', () => {
    expect(parseCardBlock('<CardGroup cols="3"><Card title="a" /></CardGroup>').cols).toBe(3);
    expect(parseCardBlock('<CardGroup><Card title="a" /></CardGroup>').cols).toBe(2);
  });
  it("parses a bare <Card> outside any group", () => {
    const b = parseCardBlock('<Card title="Solo" href="./x.md">body</Card>');
    expect(b.grouped).toBe(false);
    expect(b.cards).toEqual([{ title: "Solo", icon: undefined, href: "./x.md", body: "body" }]);
  });
  it("returns no cards for non-card angle-bracket text (renders as literal)", () => {
    expect(parseCardBlock("<CardGroup cols={2}>").cards).toEqual([]);
  });
});

describe("escapeUnknownTags — MdxDoc pre-compile hardening", () => {
  it("escapes raw placeholder tags that would abort the MDX compile", () => {
    expect(escapeUnknownTags("set <meeting_id> here")).toBe("set \\<meeting_id> here");
    expect(escapeUnknownTags("if a<b then")).toBe("if a\\<b then");
    expect(escapeUnknownTags("<Unknown thing>")).toBe("\\<Unknown thing>");
  });
  it("leaves the registry vocabulary and plain html alone", () => {
    for (const s of ['<CardGroup cols={2}>', '<Card title="x" />', "</Card>", "<Note>", "<br/>", "<div>", "<summary>"])
      expect(escapeUnknownTags(s)).toBe(s);
  });
});

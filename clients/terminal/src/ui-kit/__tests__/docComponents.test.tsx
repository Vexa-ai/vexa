/** The Mintlify dialect on a page, in both renderers (MdxDoc and the plain-Markdown fallback), and
 *  what raw HTML may NOT do there (2026-10-18). */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { Markdown } from "../Markdown";
import { MdxDoc } from "../MdxDoc";
import { directivesToComponents, parseDocComponents, sanitizeDocHtml } from "../docComponents";

afterEach(cleanup);

const ALL = `# Page

<Note>A note.</Note>

<Tip title="Pro tip">Use the tip.</Tip>

<Info>Some info.</Info>

<Check>It passed.</Check>

<Warning>

Careful **now**.

</Warning>

<Steps>
<Step title="First">

- [x] done item
- [ ] open item

</Step>
<Step title="Second">Second body.</Step>
</Steps>

<CardGroup cols={2}>
  <Card title="Alpha" href="alpha.md">About alpha.</Card>
  <Card title="Beta" />
</CardGroup>

<AccordionGroup>
<Accordion title="More detail">Hidden detail.</Accordion>
</AccordionGroup>

<Tabs>
<Tab title="One">Tab one body.</Tab>
<Tab title="Two">Tab two body.</Tab>
</Tabs>

<Frame caption="A frame">Framed text.</Frame>

<CodeGroup>

\`\`\`bash
echo hi
\`\`\`

\`\`\`python
print("hi")
\`\`\`

</CodeGroup>

<ParamField path="limit" type="number">How many rows.</ParamField>

<Mystery>inner of an unknown tag</Mystery>

\`\`\`
<Steps> stays literal in a fence
\`\`\`
`;

const RAW = /<\/?(Note|Tip|Info|Check|Warning|Steps|Step|CardGroup|Card|AccordionGroup|Accordion|Tabs|Tab|Frame|CodeGroup|ParamField|Mystery)\b|\[[ xX]\] /;

const HOSTILE = `Hello.

<script>window.__pwned = 1</script>

<div onclick="window.__pwned = 2">click text</div>

<iframe src="https://evil.example/x"></iframe>

<IFRAME SRC="https://evil.example/y"></IFRAME> <frame src="https://evil.example/f">

<a href="javascript:window.__pwned=3">bad link</a>

<img src="x" onerror="window.__pwned=4" />

<Note onClick="window.__pwned=5">safe note</Note>
`;

const prose = (c: HTMLElement) => (c.textContent ?? "").replace("<Steps> stays literal in a fence", "");

function expectSafe(container: HTMLElement) {
  expect(container.querySelector("script, iframe, object, embed, style")).toBeNull();
  for (const el of container.querySelectorAll("*")) {
    for (const attr of el.getAttributeNames()) expect(attr.toLowerCase().startsWith("on")).toBe(false);
    expect(el.getAttribute("href") ?? "").not.toMatch(/^\s*javascript:/i);
    expect(el.getAttribute("src") ?? "").not.toMatch(/^\s*javascript:/i);
  }
  const text = container.textContent ?? "";
  expect(text).not.toMatch(/__pwned|<script|<iframe|evil\.example|onclick|onerror/i);
  expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
}

describe("docComponents — passes", () => {
  it("parses known components into a tree; an unknown one is transparent; fences stay literal", () => {
    const tree = parseDocComponents('<Steps>\n<Step title="A">x <Odd>y</Odd></Step>\n</Steps>\n```\n<Step>\n```\n');
    expect(tree[0]).toMatchObject({ t: "el", name: "Steps" });
    const step = (tree[0] as { children: unknown[] }).children.find((c) => (c as { t: string }).t === "el");
    expect(step).toMatchObject({ name: "Step", attrs: { title: "A" } });
    expect(JSON.stringify(step)).toContain("y");
    expect(JSON.stringify(step)).not.toContain("Odd");
    expect(tree[tree.length - 1]).toMatchObject({ t: "text" });
    expect((tree[tree.length - 1] as { text: string }).text).toContain("```\n<Step>\n```");
  });
  it("drops a stray close and closes what is left open", () => {
    const tree = parseDocComponents("</Warning>\n<Note>open to the end");
    expect(tree).toHaveLength(2);
    expect(tree[1]).toMatchObject({ t: "el", name: "Note" });
  });
  it("never evaluates an attribute expression", () => {
    const tree = parseDocComponents('<CardGroup cols={2} x={alert(1)} onClick="y"></CardGroup>');
    expect((tree[0] as { attrs: Record<string, string> }).attrs).toEqual({ cols: "2" });
  });
  it("turns :::callout and :::columns into components", () => {
    const out = directivesToComponents(":::columns\nleft\n---\nright\n:::\n:::callout\nheads up\n:::\n:::stats source=k.json\n:::");
    expect(out).toMatch(/<Columns>\s*<Column>\s*left\s*<\/Column>\s*<Column>\s*right\s*<\/Column>\s*<\/Columns>/);
    expect(out).toMatch(/<Note>\s*heads up\s*<\/Note>/);
    expect(out).not.toMatch(/:::/);
  });
  it("strips script, iframe, on…= and javascript: but leaves fences and inline code alone", () => {
    const out = sanitizeDocHtml('a <script>x()</script> <div onclick="x()">t</div> <iframe src=y></iframe> <a href="javascript:x()">l</a> `<script>`\n```\n<script>ok</script>\n```');
    expect(out).not.toMatch(/x\(\)<\/script>|onclick|<iframe|javascript:/);
    expect(out).toContain("`<script>`");
    expect(out).toContain("<script>ok</script>");
  });
});

describe("the plain renderer (the fallback, and the workspace page)", () => {
  it("draws every component; nothing prints as a tag", () => {
    const { container } = render(<Markdown>{ALL}</Markdown>);
    expect(prose(container)).not.toMatch(RAW);
    expect(container.querySelectorAll(".vx-doc-callout")).toHaveLength(5);
    expect(container.querySelectorAll(".vx-doc-step")).toHaveLength(2);
    expect(screen.getByText("First")).toBeTruthy();
    expect(screen.getAllByRole("img", { name: /^Done: / })).toHaveLength(1);
    expect(screen.getAllByRole("img", { name: /^Open: / })).toHaveLength(1);
    expect(screen.getByText("Alpha")).toBeTruthy();
    expect(container.querySelector("details.vx-doc-accordion summary")?.textContent).toBe("More detail");
    expect(screen.getByRole("tab", { name: "One" })).toBeTruthy();
    expect(screen.getByText("Tab one body.")).toBeTruthy();
    expect(container.querySelector("figure.vx-doc-frame figcaption")?.textContent).toBe("A frame");
    expect(screen.getByRole("tab", { name: "bash" })).toBeTruthy();
    expect(screen.getByText("limit")).toBeTruthy();                 // an unknown component keeps its name
    expect(screen.getByText(/inner of an unknown tag/)).toBeTruthy();
    expect(container.textContent).toContain("<Steps> stays literal in a fence");
  });
  it("raw HTML cannot run and does not show", () => {
    const { container } = render(<Markdown>{HOSTILE}</Markdown>);
    expectSafe(container);
    expect(screen.getByText("safe note")).toBeTruthy();
  });
});

describe("MdxDoc (the compiled page)", () => {
  it("draws every component; nothing prints as a tag", async () => {
    const { container } = render(<MdxDoc outline={false}>{ALL}</MdxDoc>);
    await waitFor(() => expect(container.querySelector(".vx-doc-steps")).toBeTruthy());
    expect(container.textContent).not.toMatch(/simplified rendering/);
    expect(prose(container)).not.toMatch(RAW);
    expect(container.querySelectorAll(".vx-doc-callout")).toHaveLength(5);
    expect(container.querySelectorAll(".vx-doc-step")).toHaveLength(2);
    expect(container.querySelectorAll("input[type=checkbox]")).toHaveLength(2);
    expect(container.querySelector("details.vx-doc-accordion")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Two" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "python" })).toBeTruthy();
    expect(container.querySelector("figure.vx-doc-frame")).toBeTruthy();
    expect(screen.getByText(/inner of an unknown tag/)).toBeTruthy();
  });
  it("renders :::columns and :::callout too", async () => {
    const { container } = render(<MdxDoc outline={false}>{":::columns\nLeft side\n---\nRight side\n:::\n\n:::callout\nHeads up\n:::\n"}</MdxDoc>);
    await waitFor(() => expect(container.querySelector(".vx-doc-columns")).toBeTruthy());
    expect(container.querySelectorAll(".vx-doc-column")).toHaveLength(2);
    expect(container.querySelector(".vx-doc-callout")?.textContent).toMatch(/Heads up/);
    expect(container.textContent).not.toMatch(/:::/);
  });
  it("raw HTML cannot run and does not show", async () => {
    const { container } = render(<MdxDoc outline={false}>{HOSTILE}</MdxDoc>);
    await waitFor(() => expect(screen.getByText("safe note")).toBeTruthy());
    expectSafe(container);
  });
});

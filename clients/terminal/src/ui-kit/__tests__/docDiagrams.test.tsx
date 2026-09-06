/** A ```mermaid FENCE IS A PICTURE (Vexa-ai/vexa#1617).
 *
 *  Founder, 2026-09-06, to the setup agent: *"no we need to see diagram here"*. The agent wrote a
 *  `flowchart TB` into `_global/STRUCTURE.md` and reported the diagram was there; the admin opened
 *  the page and saw the source, because no renderer in the terminal knew what a mermaid fence was.
 *
 *  Four claims, each with a plausible wrong answer that would otherwise have shipped:
 *
 *   1. the fence draws — in the DOC view (MdxDoc), in the plain-Markdown fallback (which is also the
 *      still-streaming chat bubble), and under the fence in the pages EDITOR. One component, three
 *      surfaces, the same rule `DocImage` follows for `![alt](src)`;
 *   2. a fence that does not parse keeps its SOURCE and says why underneath. The obvious
 *      implementation swallows the error and paints an empty box, which is the one outcome worse
 *      than the defect being fixed: the reader loses even the text the agent wrote;
 *   3. a PLAIN fence is untouched. This renderer paints every page in the product, and a shell
 *      snippet that started rendering as something else would be a regression wearing a feature's
 *      clothes;
 *   4. the colours come from the page's own tokens, so the diagram changes with the theme rather
 *      than sitting on it as a foreign white rectangle.
 *
 *  The library is REAL here, not a mock — the parser messages asserted below are mermaid's own, and
 *  a mocked renderer would prove only that we can call a function we wrote. jsdom implements no SVG
 *  measurement, so the three geometry methods every layout engine needs are stubbed; that is a gap
 *  in the test DOM, not a seam in the product.
 */
import { describe, it, expect, vi, beforeAll, afterEach } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { Markdown } from "../Markdown";
import { MdxDoc } from "../MdxDoc";
import { isMermaidFence } from "../docDiagrams";
import { mermaidDecorations } from "../../minutes/mermaidPreview";
import type { EditorView } from "@codemirror/view";

// the chips resolve against a workspace snapshot; nothing here is about them, so the world is empty
vi.mock("../../surfaces/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async () => []),
  readActiveSet: vi.fn(async () => ({ subject: "57", active: [] })),
  listSharedMemberships: vi.fn(async () => []),
}));

beforeAll(async () => {
  const svg = (globalThis as unknown as { SVGElement: { prototype: Record<string, unknown> } }).SVGElement.prototype;
  svg.getBBox = () => ({ x: 0, y: 0, width: 100, height: 20 });
  svg.getComputedTextLength = () => 100;
  svg.getScreenCTM = () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0, inverse: () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }) });
  // Pay the library's one-time load HERE (Vexa-ai/vexa#1625). `docDiagrams` reaches mermaid through
  // a lazy `import("mermaid")` cached at module scope — property 2 of the module, and the reason a
  // page without a diagram downloads nothing — so the FIRST render in this file pays ~1 MB of
  // parser, layout and renderer and every later one resolves against it. On this laptop that first
  // render lands in ~690 ms and the 1 s default of `waitFor` covers it; on a CI runner carrying 137
  // other suites it does not, and the row below failed on every run of this branch with a DOM that
  // was the still-loading SOURCE block — no `data-mermaid-error`, nothing wrong with the product,
  // just a machine-speed difference read as a defect. A hook may take as long as it needs, so
  // hoisting the import leaves each assertion below measuring the render it is actually about.
  await import("mermaid");
}, 120_000);

afterEach(() => { cleanup(); document.documentElement.removeAttribute("data-theme"); });

const DIAGRAM = ["```mermaid", "flowchart TB", "  Gateway --> Bot", "  Bot --> Transcoder", "```", ""].join("\n");
const BROKEN = ["```mermaid", "flowchart TB", "  Gateway -->", "```", ""].join("\n");
const SHELL = ["```bash", "docker compose up -d", "```", ""].join("\n");

/** The drawn diagram. `waitFor` retries a callback that THROWS, so this asserts inside rather than
 *  returning a null the runner would accept — mermaid renders across several ticks. The budget is
 *  raised off the 1 s default because it is not a deadline anyone is asserting: `waitFor` returns
 *  the moment the SVG exists (~40 ms once the library is loaded in `beforeAll`), so the number only
 *  decides how slow a machine may be before a passing render is called a failure. */
const drawn = async (root: HTMLElement): Promise<SVGElement> =>
  waitFor(() => {
    const el = root.querySelector<SVGElement>("[data-mermaid-diagram] svg");
    expect(el).toBeTruthy();
    return el!;
  }, { timeout: 4_000 });

describe("the fence, in every renderer", () => {
  it("MdxDoc — the renderer the pages panel opens every page with — draws it", async () => {
    const { container } = render(<MdxDoc>{`# Deployment\n\n${DIAGRAM}`}</MdxDoc>);
    const svg = await drawn(container);
    expect(svg.querySelector("[data-mermaid-error]")).toBeNull();
    expect(container.textContent).toContain("Deployment");
  });

  it("the plain-Markdown fallback — and so the streaming chat bubble — draws the same fence", async () => {
    const { container } = render(<Markdown>{DIAGRAM}</Markdown>);
    await drawn(container);
  });

  it("the pages editor draws it under the closing fence", async () => {
    const set = mermaidDecorations(`# Page\n\n${DIAGRAM}`, "dark");
    expect(set.size).toBe(1);
    const widget = set.iter().value!.spec.widget as { toDOM(v: EditorView): HTMLElement };
    const box = widget.toDOM({ requestMeasure() {} } as unknown as EditorView);
    await waitFor(() => expect(box.querySelector("svg")).toBeTruthy());
  });

  it("a fence still being typed is not a diagram yet — nothing is drawn from an unclosed one", () => {
    expect(mermaidDecorations("```mermaid\nflowchart TB\n  A --> B\n", "dark").size).toBe(0);
    const { container } = render(<Markdown>{"```mermaid\nflowchart TB\n  A --> B\n"}</Markdown>);
    expect(container.querySelector("[data-mermaid-source]")).toBeNull();
    expect(container.querySelector("pre")?.textContent).toContain("flowchart TB");
  });
});

describe("a fence that does not parse keeps its source and says why", () => {
  it("MdxDoc shows the fence text and mermaid's own message under it — never a blank", async () => {
    const { container } = render(<MdxDoc>{BROKEN}</MdxDoc>);
    const message = await waitFor(() => {
      const el = container.querySelector<HTMLElement>("[data-mermaid-error]");
      expect(el).toBeTruthy();
      return el!;
    });
    expect(message.textContent).toContain("Parse error");
    expect(container.querySelector("[data-mermaid-source] pre")?.textContent).toContain("Gateway -->");
    expect(container.querySelector("svg")).toBeNull();
  });

  it("the plain-Markdown fallback fails the same way", async () => {
    const { container } = render(<Markdown>{BROKEN}</Markdown>);
    await waitFor(() => expect(container.querySelector("[data-mermaid-error]")?.textContent).toContain("Parse error"));
    expect(container.querySelector("[data-mermaid-source] pre")?.textContent).toContain("flowchart TB");
  });

  it("the editor says it in the widget rather than drawing an empty box", async () => {
    const set = mermaidDecorations(BROKEN, "dark");
    const widget = set.iter().value!.spec.widget as { toDOM(v: EditorView): HTMLElement };
    const box = widget.toDOM({ requestMeasure() {} } as unknown as EditorView);
    await waitFor(() => expect(box.textContent).toContain("Parse error"));
    expect(box.querySelector("svg")).toBeNull();
  });
});

describe("every other fence is untouched", () => {
  it("a shell block stays a code block in both renderers, and draws nothing", async () => {
    const { container: mdx } = render(<MdxDoc>{SHELL}</MdxDoc>);
    await waitFor(() => expect(mdx.querySelector("pre")?.textContent).toContain("docker compose up -d"));
    expect(mdx.querySelector("[data-mermaid-diagram]")).toBeNull();
    expect(mdx.querySelector("[data-mermaid-source]")).toBeNull();

    const { container: plain } = render(<Markdown>{SHELL}</Markdown>);
    expect(plain.querySelector("pre")?.textContent).toContain("docker compose up -d");
    expect(plain.querySelector("[data-mermaid-diagram]")).toBeNull();

    expect(mermaidDecorations(SHELL, "dark").size).toBe(0);
  });

  it("a ``` inside a non-diagram block does not make the rest of the file a diagram", () => {
    const doc = ["```markdown", "```mermaid", "```", "```", ""].join("\n");
    expect(mermaidDecorations(doc, "dark").size).toBe(0);
  });

  it("only the first word of the info string names the language", () => {
    expect(isMermaidFence("mermaid")).toBe(true);
    expect(isMermaidFence("  Mermaid  ")).toBe(true);
    expect(isMermaidFence('mermaid title="deployment"')).toBe(true);
    expect(isMermaidFence("bash")).toBe(false);
    expect(isMermaidFence("")).toBe(false);
    expect(isMermaidFence(undefined)).toBe(false);
  });
});

describe("the diagram is painted in the page's own theme", () => {
  it("day mode and dark mode produce different colours, each from its own token set", async () => {
    const { container: dark } = render(<Markdown>{DIAGRAM}</Markdown>);
    const darkSvg = (await drawn(dark)).outerHTML;
    cleanup();

    document.documentElement.setAttribute("data-theme", "light");
    const { container: light } = render(<Markdown>{DIAGRAM}</Markdown>);
    const lightSvg = (await drawn(light)).outerHTML;

    expect(lightSvg).not.toEqual(darkSvg);
    expect(darkSvg).toContain("#ededf0");    // --t1, dark
    expect(lightSvg).toContain("#1a1a1f");   // --t1, day mode
    expect(lightSvg).not.toContain("#ededf0");
  });
});

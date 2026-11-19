// @vitest-environment node
/** The pages' policy refuses every style ATTRIBUTE (`style-src-attr 'none'`, contentSecurityPolicy.ts):
 *  a `style` prop applies only when React writes it on the client, through the CSSOM. So the first
 *  frame the SERVER renders for each page carries none. This renders that frame the way the server
 *  does (react-dom/server, no window) and reads the HTML. The browser proof, with the policy on and
 *  every key page drawn, is scripts/style-attr-sweep.mjs. */
import { createElement, type ComponentType } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push() {}, replace() {}, refresh() {}, back() {}, prefetch() {} }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
  useParams: () => ({}),
  notFound: () => { throw new Error("notFound"); },
  redirect: () => { throw new Error("redirect"); },
}));

/** Every `style="…"` in the HTML, with the tag it sits on. */
const styleAttributes = (html: string) => html.match(/<[a-zA-Z][^>]*\sstyle="[^"]*"/g) ?? [];

const params = <T,>(v: T) => Promise.resolve(v);
const PAGES: Array<[string, () => Promise<{ default: ComponentType<never> }>, Record<string, unknown>]> = [
  ["/ (the workbench)", () => import("../page"), {}],
  ["/w/…", () => import("../w/[...ref]/page"), {}],
  ["/meetings/…", () => import("../meetings/[meetingId]/page"), {}],
  ["/join", () => import("../join/page"), {}],
  ["/mdx-demo", () => import("../mdx-demo/page"), {}],
  ["/p/…", () => import("../p/[handle]/page"), { params: params({ handle: "x" }) }],
  ["/r/…", () => import("../r/[kind]/[...id]/page"), {}],
  ["404", () => import("../not-found"), {}],
  ["/debug/transcript", () => import("../debug/transcript/page"), {}],
  ["/debug/teams-pipeline", () => import("../debug/teams-pipeline/page"), {}],
];

describe("what the server renders carries no style attribute", () => {
  for (const [name, load, props] of PAGES) {
    it(name, async () => {
      const Page = (await load()).default as ComponentType<Record<string, unknown>>;
      const html = renderToString(createElement(Page, props));
      expect(html.length, "the page rendered something").toBeGreaterThan(0);
      expect(styleAttributes(html)).toEqual([]);
    }, 60_000);   // the workbench's import graph is the whole client
  }

  it("the sign-in gate's first frame, before the session probe answers", async () => {
    const { AuthGate } = await import("../AuthGate");
    expect(styleAttributes(renderToString(createElement(AuthGate, null, createElement("div", null, "in"))))).toEqual([]);
  });

  it("a document with a diagram, before mermaid draws (the fence as its source)", async () => {
    const { MdxDoc } = await import("../../ui-kit/MdxDoc");
    const md = "# T\n\n<CardGroup cols={3}><Card title=\"a\" href=\"x.md\">b</Card></CardGroup>\n\n[[Someone]]\n\n```mermaid\nflowchart LR\n  A --> B\n```\n";
    const html = renderToString(createElement(MdxDoc, null, md));
    expect(html).toContain("data-mermaid-source");
    expect(styleAttributes(html)).toEqual([]);
  });

  it("negative control: a style prop IS a style attribute on the server", () => {
    expect(styleAttributes(renderToString(createElement("div", { style: { height: 1 } })))).toHaveLength(1);
  });
});

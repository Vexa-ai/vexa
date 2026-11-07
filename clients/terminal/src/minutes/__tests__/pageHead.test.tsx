/** A PLAIN PAGE SAYS WHAT IT IS AND WHERE IT LIVES; "MENTIONED IN" IS SHORT AND TRUE (founder,
 *  live app.dev 2026-10-15 on `finos`: no title, "it does not say now what its path is", and a
 *  "Mentioned in" wall of README ×40 with an owner's address on each). */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { PageHead, pageTitle } from "../PageHead";
import { mentions, MENTIONS_SHOWN } from "../../ui-kit/Backlinks";

afterEach(() => cleanup());

describe("the page's head", () => {
  it("the title is the H1, else the frontmatter title, else the file name made readable", () => {
    expect(pageTitle("# FINOS\n\nThe open-source foundation.", "kg/entities/companies/finos.md")).toEqual({ title: "FINOS", body: "The open-source foundation." });
    expect(pageTitle("---\ntitle: FINOS\n---\nThe foundation.", "kg/x.md").title).toBe("FINOS");
    expect(pageTitle("The foundation.", "kg/entities/companies/open-source-fund.md").title).toBe("Open source fund");
  });
  it("one quiet line: workspace › folders, each opening that folder", () => {
    const onFolder = vi.fn();
    const { container } = render(<PageHead title="FINOS" path="kg/entities/companies/finos.md" onFolder={onFolder} />);
    expect(container.querySelector("[data-doc-title]")?.textContent).toBe("FINOS");
    expect(container.querySelector("[data-page-path]")?.textContent).toBe("Personal›kg›entities›companies");
    fireEvent.click(container.querySelector('[data-path-seg="kg/entities"]')!);
    expect(onFolder).toHaveBeenCalledWith("kg/entities");
    expect(container.querySelectorAll("[data-page-head] svg, [data-page-head] img")).toHaveLength(0);   // no icon band
  });
});

describe("Mentioned in", () => {
  const wall = [
    ...Array.from({ length: 40 }, (_, i) => ({ slug: `d${i}`, path: "README.md", title: "README", workspace: "dmitry@vexa.ai" })),
    { slug: "_global", path: "CLAUDE.md", title: "CLAUDE", workspace: "admin@vexa.ai" },
    { slug: "biz", path: "AGENTS.md", title: "AGENTS", workspace: "biz" },
    { slug: "biz", path: "kg/index.md", title: "Index", workspace: "biz" },
    { slug: "biz", path: "kg/notes/cadence.md", title: "Cadence", workspace: "biz" },
    { slug: "biz", path: "kg/notes/cadence.md", title: "Cadence", workspace: "biz" },
    ...["Analysis", "Company page", "Pricing", "Pilot", "Roadmap", "Board"].map((t) => ({ slug: "finos", path: `kg/${t}.md`, title: t, workspace: "FINOS" })),
    { slug: "desk", path: "kg/me.md", title: "Me", workspace: "dmitry@vexa.ai" },
  ];
  it("real pages only, one each, a workspace named only when it is another one and never an address", () => {
    const got = mentions(wall, "finos");
    expect(got.map((r) => r.title)).toEqual(["Cadence", "Analysis", "Company page", "Pricing", "Pilot", "Roadmap", "Board", "Me"]);
    expect(got.find((r) => r.title === "Cadence")?.elsewhere).toBe(true);
    expect(got.find((r) => r.title === "Pilot")?.elsewhere).toBe(false);
    expect(got.some((r) => String(r.workspace ?? "").includes("@"))).toBe(false);
    expect(MENTIONS_SHOWN).toBe(5);
  });
});

describe("Mentioned in, live (founder 2026-10-15: a wall with no cap, and a chip that did nothing)", () => {
  const rows = [
    ...["Analysis", "Cadence", "Company page", "Profile diff", "First fire", "Pricing", "Pilot"].map((t, i) =>
      ({ slug: i % 2 ? "biz" : "personal", path: `kg/${t.toLowerCase().replace(/ /g, "-")}.md`, title: t, workspace: i % 2 ? "biz" : "Personal" })),
  ];
  it("five shown, the rest behind N more and back; every chip opens exactly its page, in any workspace", async () => {
    vi.resetModules();
    vi.doMock("../../data/workspaceApi", () => ({ pageBacklinks: vi.fn(async () => rows) }));
    class IO { constructor(private cb: (e: { isIntersecting: boolean }[]) => void) {} observe() { this.cb([{ isIntersecting: true }]); } disconnect() {} }
    vi.stubGlobal("IntersectionObserver", IO);
    const { Backlinks } = await import("../../ui-kit/Backlinks");
    const { DocMetaContext } = await import("../../ui-kit/docRefs");
    const { OPEN_PAGE_EVENT } = await import("../../platform/events");
    const opened: unknown[] = [];
    const on = (e: Event) => opened.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_PAGE_EVENT, on);
    const { container, findAllByRole } = render(
      <DocMetaContext.Provider value={{ path: "kg/entities/companies/finos.md", slug: "ait" }}><Backlinks /></DocMetaContext.Provider>);
    expect(await findAllByRole("link")).toHaveLength(5);
    expect(container.querySelector("[data-backlinks-more]")?.textContent).toBe("2 more");
    fireEvent.click(container.querySelector("[data-backlinks-more]")!);
    expect(container.querySelectorAll("[data-mention]")).toHaveLength(7);
    fireEvent.click(container.querySelector("[data-backlinks-fewer]")!);
    expect(container.querySelectorAll("[data-mention]")).toHaveLength(5);
    fireEvent.click(container.querySelector('[data-mention="personal:kg/analysis.md"]')!);
    fireEvent.click(container.querySelector('[data-mention="biz:kg/cadence.md"]')!);
    expect(opened).toEqual([{ workspace: "", path: "kg/analysis.md" }, { workspace: "biz", path: "kg/cadence.md" }]);
    window.removeEventListener(OPEN_PAGE_EVENT, on);
    vi.unstubAllGlobals();
    vi.doUnmock("../../data/workspaceApi");
  });
  it("a page from a workspace the chat does not read says so, quietly", () => {
    const { container, rerender } = render(<PageHead title="Analysis" path="kg/analysis.md" reads={["ait", "_global"]} onFolder={() => {}} />);
    expect(container.querySelector("[data-outside-scope]")?.textContent).toContain("not read by this chat");
    rerender(<PageHead title="Analysis" path="kg/analysis.md" slug="ait" reads={["ait", "_global"]} onFolder={() => {}} />);
    expect(container.querySelector("[data-outside-scope]")).toBeNull();
  });
});

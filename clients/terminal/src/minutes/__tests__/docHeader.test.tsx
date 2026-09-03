/** The pages panel's DOC HEADER — the row that says what is in front and what can be done to it.
 *
 *  Three claims worth guarding, because each has a wrong answer that looks plausible on screen:
 *  the header names the FILE (not the tab label, which is the name with the extension eaten); the
 *  `</>` lens shows the markdown the renderer was given rather than opening a second editor; and a
 *  meeting canvas — which has no file to read, copy or edit — gets no header row at all instead of
 *  an empty one advertising controls that would do nothing.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { PagesPanel } from "../PagesPanel";
import type { Page } from "../types";

const PATH = "drafts/2026-09-01-vexa-prd.md";
const BODY = "# The PRD\n\nOne paragraph of prose.";
const pages: Page[] = [{ path: PATH, label: "2026-09-01-vexa-prd" }];

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const doc = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
  render(<PagesPanel pages={pages} docPath={PATH} onOpen={() => {}} body={BODY} {...over} />);

describe("doc header — filename prominent, location subdued", () => {
  it("names the FILE, extension and all — and ONLY the file (PRD decision 28)", () => {
    // The title row used to carry the folder trail beside the name, which the breadcrumb directly
    // below already shows and can navigate. Founder: *"duplicated paths"*. One path line: the name
    // belongs here, the path belongs there.
    const { container } = doc();
    expect(container.querySelector("[data-doc-name]")?.textContent).toBe("2026-09-01-vexa-prd.md");
    expect(container.querySelector("[data-doc-where]")).toBeNull();
  });

  it("the path is shown ONCE, by the breadcrumb, and it is the navigable one", () => {
    const { container } = doc({ docSlug: "acme", pages: [{ path: PATH, slug: "acme", label: "prd" }] });
    // the workspace and every folder are still reachable — as buttons, which is the point of
    // keeping the breadcrumb rather than the dead text beside the title
    const crumbs = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(crumbs).toContain("acme");
    expect(crumbs).toContain("drafts");
    expect(container.querySelectorAll("[data-doc-where]")).toHaveLength(0);
  });

  it("the utilities are grouped in the header, and no longer in the tab strip", () => {
    const { container } = doc();
    for (const act of ["raw", "copy", "edit"]) {
      expect(container.querySelector(`[data-doc-act="${act}"]`)).toBeTruthy();
    }
    // the 46px band is the tab strip's alone now — the Edit button used to compete for it
    expect(screen.queryByRole("button", { name: "Edit" })?.closest("[data-doc-act]")).toBeTruthy();
  });
});

describe("the `</>` lens", () => {
  it("swaps the rendered document for its source, and back", () => {
    const { container } = doc();
    expect(container.querySelector("[data-doc-raw]")).toBeNull();

    fireEvent.click(container.querySelector('[data-doc-act="raw"]') as HTMLElement);
    const raw = container.querySelector("[data-doc-raw]");
    expect(raw?.textContent).toBe(BODY);                        // the markdown, not the rendering
    expect(raw?.tagName).toBe("PRE");                           // a lens…
    expect(container.querySelector(".cm-editor")).toBeNull();   // …never a second editor

    fireEvent.click(container.querySelector('[data-doc-act="raw"]') as HTMLElement);
    expect(container.querySelector("[data-doc-raw]")).toBeNull();
  });

  it("opening another document returns to the rendered view", () => {
    const { container, rerender } = doc();
    fireEvent.click(container.querySelector('[data-doc-act="raw"]') as HTMLElement);
    expect(container.querySelector("[data-doc-raw]")).toBeTruthy();

    rerender(<PagesPanel pages={pages} docPath="README.md" onOpen={() => {}} body={BODY} />);
    expect(container.querySelector("[data-doc-raw]")).toBeNull();
  });
});

describe("what the header stands down for", () => {
  it("a meeting canvas gets no header row — nothing in the group applies to it", () => {
    const { container } = render(
      <PagesPanel pages={[{ kind: "meeting", path: "42", label: "Standup" }]}
        docPath="42" docKind="meeting" onOpen={() => {}} body={null} />,
    );
    expect(container.querySelector("[data-doc-name]")).toBeNull();
    expect(container.querySelector('[data-doc-act="edit"]')).toBeNull();
  });

  it("a folder listing is addressed by the breadcrumb, not by a document header", () => {
    const { container } = doc({ listing: { prefix: "drafts", dirs: [], files: ["a.md"] } });
    expect(container.querySelector("[data-doc-name]")).toBeNull();
  });
});

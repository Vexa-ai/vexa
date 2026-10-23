/** The pages panel's DOC HEADER — the row that says what is in front and what can be done to it.
 *
 *  Three claims worth guarding, because each has a wrong answer that looks plausible on screen:
 *  the header names the FILE (not the tab label, which is the name with the extension eaten); it
 *  names it ONCE, the breadcrumb below having stopped at the folder (founder ruling 2026-09-06:
 *  *"no need to duplicate doc name"*); and a meeting canvas — which has no file to read, copy or
 *  edit — gets no header row at all instead of an empty one advertising controls that would do
 *  nothing.
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

describe("a page opens with its title and content (UI decisions 12)", () => {
  it("no name row, no path row, no band of icons above the page", async () => {
    const { container } = doc({ docSlug: "acme", pages: [{ path: PATH, slug: "acme", label: "prd" }] });
    expect(container.querySelector("[data-doc-name]")).toBeNull();
    expect(container.querySelector("[data-crumb]")).toBeNull();
    expect(container.querySelector('[data-doc-act="copy"]')).toBeNull();     // not until "…" is opened
    await screen.findByText("One paragraph of prose.");
  });

  it("copy, edit, details, where it lives and delete are one \"…\" at the page's corner", () => {
    const { container } = doc({ docSlug: "acme", pages: [{ path: PATH, slug: "acme", label: "prd" }] });
    fireEvent.click(container.querySelector("[data-doc-more]")!);
    const items = [...document.querySelectorAll("[role^=menuitem]")].map((b) => b.getAttribute("data-doc-act"));
    expect(items).toEqual(expect.arrayContaining(["copy", "edit", "details", "where"]));
    expect(document.querySelector('[data-doc-act="where"]')?.textContent).toContain("drafts");
  });

  it("Edit opens the editor with a compact Cancel · Save row", () => {
    const { container } = doc();
    fireEvent.click(container.querySelector("[data-doc-more]")!);
    fireEvent.click(document.querySelector('[data-doc-act="edit"]')!);
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
  });

  it("a FOLDER keeps its last crumb — there is no header above a listing to say it twice", () => {
    const { container } = doc({ listing: { prefix: "drafts", dirs: [], files: ["a.md"] } });
    expect(container.querySelector("[data-doc-name]")).toBeNull();
    expect(container.textContent).toContain("drafts");
  });
});

describe("the `</>` raw-markdown toggle — removed (founder ruling 2026-09-06)", () => {
  it("is not in the header, and there is no way left to reach the raw pre", () => {
    // *"remove raw markdown button"*. It answered a question a reader of a document does not ask,
    // and Edit already shows the source to anyone who does.
    const { container } = doc();
    expect(container.querySelector('[data-doc-act="raw"]')).toBeNull();
    expect(container.querySelector("[data-doc-raw]")).toBeNull();
    expect(screen.queryByLabelText("Toggle markdown source")).toBeNull();
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

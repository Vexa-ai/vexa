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
    for (const act of ["copy", "edit"]) {
      expect(container.querySelector(`[data-doc-act="${act}"]`)).toBeTruthy();
    }
    // the 46px band is the tab strip's alone now — the Edit button used to compete for it
    expect(screen.queryByRole("button", { name: "Edit" })?.closest("[data-doc-act]")).toBeTruthy();
  });

  it("says the document's name ONCE — the crumb stops at the folder it lives in", () => {
    // Founder ruling 2026-09-06, on a screenshot showing `academy-software-foundation.md` in the
    // header and `_global › kg › entities › company › academy-software-foundation.md` under it:
    // *"no need to duplicate doc name"*. The name belongs to the header; the path belongs to the
    // crumb, and the crumb's last segment WAS the name.
    const { container } = doc();
    expect(container.textContent!.split("2026-09-01-vexa-prd.md")).toHaveLength(2);   // one occurrence
    expect(container.querySelector("[data-doc-name]")?.textContent).toBe("2026-09-01-vexa-prd.md");
    const crumbs = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(crumbs).toContain("drafts");                                              // still walkable
    expect(crumbs).not.toContain("2026-09-01-vexa-prd.md");
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

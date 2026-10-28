/** Links across workspaces, by slug (#1783; founder: "cross workspace links do not work"). */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { cleanup, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { invalidateWsLinkCaches } from "../wsLinks";
import { WsLink, isQualifiedRef } from "../WsLink";
import { climbedOutRef } from "../docLinks";

function answer(results: unknown[]) {
  globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ results }) }) as unknown as Response) as unknown as typeof fetch;
}
beforeEach(() => invalidateWsLinkCaches());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("the grammar", () => {
  it("a slug-qualified ref and the vexa:// form are cross-workspace; a sentence with a colon is not", () => {
    expect(isQualifiedRef("biz:graph/sg/Oss-Led-Vendor-Pull-Growth")).toBe(true);
    expect(isQualifiedRef("vexa://ws/biz/graph/sg/x.md")).toBe(true);
    expect(isQualifiedRef("https://example.com/x")).toBe(false);
    expect(isQualifiedRef("Note: call back tomorrow")).toBe(false);
  });
  it("a relative link that climbs out of its workspace names the sibling workspace", () => {
    expect(climbedOutRef("../../../../biz/graph/sg/x.md", "kg/entities/contacts/p.md")).toBe("biz:graph/sg/x.md");
    expect(climbedOutRef("../../../biz/graph/sg/x.md", "kg/entities/contacts/p.md")).toBeNull();   // still inside
    expect(climbedOutRef("../company/acme.md", "kg/entities/contacts/p.md")).toBeNull();
    expect(climbedOutRef("notes/a.md", "kg/x.md")).toBeNull();
  });
});

describe("the chip", () => {
  it("readable: the page's title, opening it in its workspace", async () => {
    answer([{ ref: "biz:graph/sg/oss-led-vendor-pull-growth", title: "OSS-led vendor pull", access: "readable", url: "/w/x/graph/sg/a.md", path: "graph/sg/a.md", slug: "biz", workspace: "Biz" }]);
    render(<WsLink refText="biz:graph/sg/oss-led-vendor-pull-growth" />);
    await waitFor(() => expect(screen.getByRole("link").textContent).toContain("OSS-led vendor pull"));
  });
  it("restricted: the ref's own words and the word restricted, never the page's title, not clickable", async () => {
    answer([{ ref: "biz:graph/sg/oss-led-vendor-pull-growth", title: "Oss Led Vendor Pull Growth", access: "not-yours", url: null, workspace: null }]);
    const { container } = render(<WsLink refText="biz:graph/sg/oss-led-vendor-pull-growth" />);
    await waitFor(() => expect(container.querySelector("[data-restricted]")).not.toBeNull());
    expect(container.textContent).toContain("restricted");
    expect(container.textContent).toContain("Oss Led Vendor Pull Growth");
    expect(screen.queryByRole("link")).toBeNull();
  });
  it("ambiguous: a small picker lists each readable match", async () => {
    answer([{ ref: "enterprise-adoption", title: "Enterprise Adoption", access: "readable", url: null, ambiguous: true,
      candidates: [{ title: "Enterprise adoption", path: "graph/sg/Enterprise-Adoption.md", slug: "biz", workspace: "Biz" },
                   { title: "Grp adoption notes", path: "notes/enterprise-adoption.md", slug: "grp", workspace: "Group" }] }]);
    render(<WsLink refText="enterprise-adoption" />);
    const chip = await screen.findByRole("button", { name: /Enterprise Adoption/ });
    fireEvent.click(chip);
    expect(screen.getByRole("menuitem", { name: /Grp adoption notes/ })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /Enterprise adoption/ })).toBeTruthy();
  });
});

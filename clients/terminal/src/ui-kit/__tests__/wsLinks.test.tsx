/** The client half of PRD decision 26: the `ws:` grammar, the canonical route, the batched
 *  resolver, and the three states a cross-workspace chip renders.
 *
 *  The claim these defend is the one the server cannot enforce alone: **a link into a workspace you
 *  do not have is not an error, and it does not look like one.**
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import {
  canonicalUrl, humanize, invalidateWsLinkCaches, isWorkspaceId, isWsRef,
  parseCanonicalUrl, parseWsRef, resolveLink,
} from "../wsLinks";
import { WsLink, WorkspaceName } from "../WsLink";
import {
  isWorkspaceRouteId, workspacePath, workspaceRouteFromPath, isWorkspacePath,
} from "../../app/workspaceRoute";

const ID = "k4m5x2q7bd";

let fetchMock: ReturnType<typeof vi.fn>;
const calls = () => fetchMock.mock.calls;
function mockJson(body: unknown, status = 200) {
  fetchMock = vi.fn(async () => ({ ok: status < 400, status, json: async () => body }) as unknown as Response);
  globalThis.fetch = fetchMock as unknown as typeof fetch;
}
beforeEach(() => invalidateWsLinkCaches());
// Explicit, because this suite has no globals:true — without it every render stays in the document
// and `screen.queryByRole` reads the PREVIOUS test's chip, which is exactly the assertion that
// matters here ("gone renders nothing clickable") answering about the wrong element.
afterEach(() => { cleanup(); vi.restoreAllMocks(); });


describe("the grammar", () => {
  it("parses the cross-workspace forms and leaves the in-workspace one alone", () => {
    expect(parseWsRef(`ws:${ID}/olga-avramenko`)).toEqual({ workspace: ID, target: "olga-avramenko" });
    expect(parseWsRef(`ws:${ID}/kg/notes/2026-03-02.md`))
      .toEqual({ workspace: ID, target: "kg/notes/2026-03-02.md" });
    expect(parseWsRef("Olga Avramenko")).toBeNull();
  });
  it("refuses a ref whose workspace is not an id — the server says what a bad ref means", () => {
    expect(parseWsRef("ws:oops/x")).toBeNull();
    expect(parseWsRef(`ws:${ID}`)).toBeNull();
    expect(parseWsRef(`ws:${ID}/`)).toBeNull();
    expect(isWsRef("ws:oops/x")).toBe(true);      // still routed to the ws renderer, which says `gone`
  });
  it("knows an id from anything else", () => {
    expect(isWorkspaceId(ID)).toBe(true);
    expect(isWorkspaceId("K4M5X2Q7BD")).toBe(false);   // one case only — never two spellings of one link
    expect(isWorkspaceId("abcdefghi1")).toBe(false);   // 0/1/8/9 are not in the alphabet
    expect(isWorkspaceId("short")).toBe(false);
  });
  it("humanizes a target for the reader who cannot open it", () => {
    expect(humanize("olga-avramenko")).toBe("Olga Avramenko");
    expect(humanize("kg/entities/person/cottalango-leon.md")).toBe("Cottalango Leon");
  });
});


describe("the canonical URL", () => {
  it("round-trips and ignores what a mail client appends", () => {
    const u = canonicalUrl(ID, "kg/entities/person/olga-avramenko.md");
    expect(u).toBe(`/w/${ID}/kg/entities/person/olga-avramenko.md`);
    expect(parseCanonicalUrl(u)).toEqual({ workspace: ID, target: "kg/entities/person/olga-avramenko.md" });
    expect(parseCanonicalUrl(`${u}?utm=mail#top`)).toEqual(parseCanonicalUrl(u));
    expect(parseCanonicalUrl("/workspaces/x/README.md")).toBeNull();
  });
  it("the route parses the same shape, and refuses a traversal", () => {
    expect(isWorkspaceRouteId(ID)).toBe(true);
    expect(workspaceRouteFromPath(`/w/${ID}/kg/INDEX.md`)).toEqual({ workspace: ID, path: "kg/INDEX.md" });
    expect(workspaceRouteFromPath(`/w/${ID}/`)).toEqual({ workspace: ID, path: "" });
    expect(workspaceRouteFromPath(`/w/${ID}/../126/kg/INDEX.md`)).toBeNull();
    // …AND A SLUG, which is not the canonical form and IS what a person pastes (#1643). It used to
    // parse as nothing at all, so the address bar's own statement produced no effect whatsoever.
    expect(isWorkspaceRouteId("pilot-b5e60c")).toBe(false);
    expect(workspaceRouteFromPath("/w/pilot-b5e60c/x")).toEqual({ workspace: "pilot-b5e60c", path: "x" });
    expect(workspaceRouteFromPath("/w/.system/x")).toBeNull();   // machinery is not addressable
    expect(workspaceRouteFromPath("/meetings/12")).toBeNull();
    expect(isWorkspacePath(`/w/${ID}`)).toBe(true);
    expect(isWorkspacePath("/")).toBe(false);
  });
  it("formats a path with spaces and refuses to build a traversal", () => {
    expect(workspacePath(ID, "kg/a b.md")).toBe(`/w/${ID}/kg/a%20b.md`);
    expect(workspacePath(ID, "../secrets")).toBe(`/w/${ID}`);
    expect(workspacePath("../etc", "x")).toBe("/");
  });
});


describe("resolution", () => {
  it("batches every ref asked for in one tick into ONE request", async () => {
    mockJson({ results: [
      { ref: `ws:${ID}/a`, title: "A", url: `/w/${ID}/kg/entities/person/a.md`, access: "readable" },
      { ref: `ws:${ID}/b`, title: "B", url: null, access: "not-yours" },
    ] });
    const [a, b] = await Promise.all([resolveLink(`ws:${ID}/a`), resolveLink(`ws:${ID}/b`)]);
    expect(calls()).toHaveLength(1);
    expect(String(calls()[0][0])).toBe("/api/links/resolve");
    expect(JSON.parse(String((calls()[0][1] as RequestInit).body)))
      .toEqual({ refs: [`ws:${ID}/a`, `ws:${ID}/b`] });
    expect(a.access).toBe("readable");
    expect(b.access).toBe("not-yours");
  });
  it("caches, so a second render of the same doc asks nothing", async () => {
    mockJson({ results: [{ ref: `ws:${ID}/a`, title: "A", url: null, access: "readable" }] });
    await resolveLink(`ws:${ID}/a`);
    await resolveLink(`ws:${ID}/a`);
    expect(calls()).toHaveLength(1);
  });
  it("a failed request is `gone`, never a rejection at a renderer", async () => {
    fetchMock = vi.fn(async () => { throw new Error("offline"); });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const r = await resolveLink(`ws:${ID}/olga-avramenko`);
    expect(r.access).toBe("gone");
    expect(r.title).toBe("Olga Avramenko");       // the last thing we know — what the writer typed
  });
  it("a ref the server did not answer is `gone` too, never undefined", async () => {
    mockJson({ results: [] });
    expect((await resolveLink(`ws:${ID}/x`)).access).toBe("gone");
  });
});


describe("the chip", () => {
  it("readable — opens the page, named by its title now", async () => {
    mockJson({ results: [{ ref: `ws:${ID}/cottalango-leon`, title: "Cottalango Leon",
      url: `/w/${ID}/kg/entities/person/cottalango-leon.md`, access: "readable",
      path: "kg/entities/person/cottalango-leon.md", slug: "grp", workspace: "ASWF DNA Project" }] });
    render(<WsLink refText={`ws:${ID}/cottalango-leon`} />);
    const chip = await screen.findByRole("link");
    expect(chip.textContent).toContain("Cottalango Leon");
    expect(chip.getAttribute("title")).toContain("ASWF DNA Project");
  });

  it("not-yours — greyed, says whose it is, and does NOT invite a click", async () => {
    mockJson({ results: [{ ref: `ws:${ID}/cottalango-leon`, title: "Cottalango Leon", url: null,
      access: "not-yours", workspace: "ASWF DNA Project" }] });
    render(<WsLink refText={`ws:${ID}/cottalango-leon`} />);
    const chip = await screen.findByTitle(/you don't have/i);
    expect(chip.textContent).toContain("Cottalango Leon");
    expect(chip.getAttribute("title")).toContain("ASWF DNA Project");
    expect(screen.queryByRole("link")).toBeNull();
    expect(chip.getAttribute("aria-disabled")).toBe("true");
  });

  it("gone — the last known title, as plain text, with nothing to click", async () => {
    mockJson({ results: [{ ref: `ws:${ID}/cottalango-leon`, title: "Cottalango Leon", url: null,
      access: "gone", workspace: null }] });
    const { container } = render(<WsLink refText={`ws:${ID}/cottalango-leon`} />);
    // The RESOLVING state also reads "Cottalango Leon" (humanized from the ref), so waiting on the
    // text would assert about the wrong frame — wait for the icon to go, which only `gone` does.
    await waitFor(() => expect(container.querySelector("svg")).toBeNull());
    expect(container.textContent).toBe("Cottalango Leon");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows the human name while it resolves, never the raw id", async () => {
    mockJson({ results: [] });
    const { container } = render(<WsLink refText={`ws:${ID}/olga-avramenko`} />);
    expect(container.textContent).toContain("Olga Avramenko");
    expect(container.textContent).not.toContain(ID);
  });
});


describe("a workspace's name, where its slug used to print (F49)", () => {
  it("renders the registry name once it lands", async () => {
    mockJson({ id: ID, name: "olga@spi.com", kind: "desk", slug: "126", access: "readable" });
    render(<WorkspaceName slug="126" />);
    expect(screen.getByText("126")).toBeTruthy();               // honest first paint: the slug
    await waitFor(() => expect(screen.getByText("olga@spi.com")).toBeTruthy());
    expect(String(calls()[0][0])).toBe("/api/workspaces/by-slug/126");
  });
  it("keeps the slug when the lookup fails — a worse label, never a blank one", async () => {
    fetchMock = vi.fn(async () => { throw new Error("offline"); });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<WorkspaceName slug="126" />);
    await waitFor(() => expect(screen.getByText("126")).toBeTruthy());
  });
});

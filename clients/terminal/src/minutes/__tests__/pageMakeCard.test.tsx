/** "Make a card" on a page's "…" — the page header's and the navigator's file row (#1783, desktop ADR
 *  D2; UI decisions 15c/15d: "Pin to home" becomes "Make a card"). Offered only while `desktop_cards`
 *  is on; the card is titled as the page and is about `page:<workspace>/<path>` (the person's own desk
 *  by their subject id); it joins the page's workspace only where the person can write it. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
vi.mock("../../ui-kit/primitives/Toast", () => ({ toast: () => {}, Toaster: () => null }));
import { setFeatureOverride } from "../../platform/features";
import { resetDesktopStore } from "../desktop/desktopApi";
import { resetOfferCache } from "../pageDelete";
import { PagesPanel } from "../PagesPanel";
import { Navigator } from "../Navigator";

const PAGE = "notes/plan.md";
type Call = { url: string; body: Record<string, unknown> | null };
let calls: Call[] = [];
const json = (status: number, body: unknown) =>
  ({ ok: status < 400, status, statusText: String(status), headers: new Headers({ "content-type": "application/json" }),
     json: async () => body, text: async () => JSON.stringify(body) }) as unknown as Response;
const DESKTOP = { version: 1, undo_version: null, proposals: [], filters: [], saved: [], hidden_workspaces: 0, presence_visibility: "off", cards_on: true,
  sections: [{ id: "workspaces", title: "Workspaces", hidden: false, collapsed: false, column: 1, span: 1, size: "normal", items: [
    { kind: "workspace", ref: "workspace:team", slug: "team", id: "team", name: "Team", workspace_kind: "shared", access: "contributor", write: true, members: [] },
  ] }] };

beforeEach(() => {
  calls = [];
  resetOfferCache();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, body: typeof init?.body === "string" ? JSON.parse(init.body) : null });
    if (url === "/api/desktop") return json(200, DESKTOP);
    if (url.startsWith("/api/desktop/card/")) return json(200, { status: "saved", card: { id: "r7" } });
    if (url.startsWith("/api/workspace/tree")) return json(200, { files: [PAGE] });
    if (url.startsWith("/api/workspace/shared")) return json(200, { memberships: [{ workspace_id: "team", role: "contributor" }] });
    if (url.startsWith("/api/workspace/active")) return json(200, { subject: "7", active: [] });
    if (url.startsWith("/api/workspaces/by-slug/")) return json(200, { id: "x", writable: false });
    if (url.startsWith("/api/auth/me")) return json(200, { is_admin: false });
    return json(404, { detail: "not found" });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); resetDesktopStore(); setFeatureOverride("all"); });

const creates = () => calls.filter((c) => c.url === "/api/desktop/card/create");

describe("the page header's …", () => {
  const panel = () => render(<PagesPanel pages={[{ path: PAGE, slug: "team", label: "plan" }]} docPath={PAGE} docSlug="team" onOpen={() => {}} body={"# Plan\n"} />);
  it("makes a card about the page, on the workspace's board", async () => {
    const { container } = panel();
    fireEvent.click(container.querySelector("[data-doc-more]")!);
    fireEvent.click(await screen.findByRole("menuitem", { name: /Make a card/ }));
    await waitFor(() => expect(creates()).toHaveLength(1));
    expect(creates()[0].body).toEqual({ title: "plan", about: "page:team/notes/plan.md", workspace: "team" });
  });
  it("offers nothing with desktop_cards off", async () => {
    setFeatureOverride({ desktop_cards: false });
    const { container } = panel();
    fireEvent.click(container.querySelector("[data-doc-more]")!);
    await screen.findByRole("menu");
    expect(screen.queryByRole("menuitem", { name: /Make a card/ })).toBeNull();
  });
});

describe("the navigator's file row …", () => {
  it("makes a card about a page on the person's own desk, by their subject id, on no board", async () => {
    render(<Navigator onOpenTab={() => {}} onClose={() => {}} workspaces={[{ key: "desk", name: "Desk", kind: "desk" }]} trees={{ desk: [PAGE] }} />);
    fireEvent.click(document.querySelector('[data-nav-ws="desk"]')!);
    for (const b of document.querySelectorAll<HTMLElement>("[data-nav-dir]")) fireEvent.click(b);
    await waitFor(() => expect(document.querySelector(`[data-nav-more="desk|${PAGE}"]`)).toBeTruthy());
    await act(async () => { fireEvent.click(document.querySelector(`[data-nav-more="desk|${PAGE}"]`)!); });
    fireEvent.click(await screen.findByRole("menuitem", { name: /Make a card/ }));
    await waitFor(() => expect(creates()).toHaveLength(1));
    expect(creates()[0].body).toEqual({ title: "plan", about: "page:7/notes/plan.md" });
  });
});

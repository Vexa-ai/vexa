/** DELETING A PAGE (founder 2026-10-11: *"we probably need a delete for a note"*).
 *
 *  What has to be true, one block each:
 *  - the page header offers **Delete** to a reader who may edit — and not to a reader, nor on
 *    `_system`, a meeting's page or a workspace README;
 *  - the confirm names the page and the workspace, its Delete button takes focus ONCE on open and
 *    keeps it across re-renders (the #1808/#1829 rule), and confirming removes the page through
 *    agent-api's `POST /api/workspace/remove`;
 *  - a refusal stays in the dialog;
 *  - every tab on the page goes; the chat's home and a meeting's pages stay;
 *  - the toast's **Undo** restores through `POST /api/workspace/restore` with the removal's commit;
 *  - a link to the deleted page resolves to nothing afterwards — the dashed "no page yet" chip;
 *  - the navigator's file row menu offers Delete… where the reader may edit, and only there.
 *
 *  The server is a fake behind `fetch`, holding one desk and one shared workspace. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, act } from "@testing-library/react";
import { PagesPanel } from "../PagesPanel";
import { Navigator } from "../Navigator";
import {
  DeletePageDialog, PageRemovedToast, dismissRemoved, dropRemovedPage, removedNow, resetOfferCache,
} from "../pageDelete";
import { invalidateDocLinkCaches, resolveDocRef } from "../../ui-kit/docLinks";
import { PAGE_REMOVED_EVENT } from "../../platform";
import type { Page } from "../types";

const PAGE = "kg/entities/project/so-vexam.md";
const BODY = "# So Vexam\n\nNot a distinct project.";

type Call = { method: string; url: string; body: Record<string, unknown> | null };
let calls: Call[] = [];
let trees: Record<string, string[]> = {};
let roles: Record<string, string> = {};
let refuse: string | null = null;

const json = (status: number, body: unknown) =>
  ({ ok: status < 400, status, statusText: String(status), headers: new Headers({ "content-type": "application/json" }),
     json: async () => body, text: async () => JSON.stringify(body) }) as unknown as Response;

beforeEach(() => {
  calls = [];
  trees = { "": ["README.md", PAGE], team: ["README.md", "notes/plan.md"] };
  roles = {};
  refuse = null;
  resetOfferCache();
  invalidateDocLinkCaches();
  dismissRemoved();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : null;
    calls.push({ method, url, body });
    const key = (s: unknown) => (typeof s === "string" ? s : "");
    if (url.startsWith("/api/workspace/remove")) {
      if (refuse) return json(403, { detail: refuse });
      const k = key(body?.slug);
      trees[k] = (trees[k] ?? []).filter((p) => p !== body?.path);
      return json(200, { path: body?.path, deleted: true, workspace: k, commit: "abc1234" });
    }
    if (url.startsWith("/api/workspace/restore")) {
      const k = key(body?.slug);
      trees[k] = [...(trees[k] ?? []), String(body?.path)];
      return json(200, { path: body?.path, restored: true, commit: "def5678" });
    }
    if (url.startsWith("/api/workspace/tree")) {
      const slug = new URL(url, "http://x").searchParams.get("slug") ?? "";
      return json(200, { files: trees[slug] ?? [] });
    }
    if (url.startsWith("/api/workspace/shared")) {
      return json(200, { memberships: Object.entries(roles).map(([workspace_id, role]) => ({ workspace_id, role })) });
    }
    if (url.startsWith("/api/workspace/active")) return json(200, { subject: "7", active: [] });
    if (url.startsWith("/api/workspaces/by-slug/")) return json(200, { id: "x", name: null, kind: null, access: "readable", writable: false });
    if (url.startsWith("/api/auth/me")) return json(200, { is_admin: false });
    return json(404, { detail: "not found" });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });


/** The page's acts are one "…" (UI decisions 12): open it, then reach the act. */
const openActs = (c: HTMLElement) => { if (!document.querySelector("[role=menu]")) fireEvent.click(c.querySelector("[data-doc-more]")!); };
const panel = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
  render(<PagesPanel pages={[{ path: PAGE, label: "so-vexam" }]} docPath={PAGE} onOpen={() => {}} body={BODY} {...over} />);

describe("the page header offers Delete where the reader may edit", () => {
  it("on the reader's own desk", async () => {
    const { container } = panel();
    await waitFor(() => { if (document.querySelector("[role=menu]")) fireEvent.click(container.querySelector("[data-doc-more]")!); openActs(container); expect(document.querySelector('[data-doc-act="delete"]')).toBeTruthy(); });
  });

  it("in a shared workspace where they are a contributor", async () => {
    roles = { team: "contributor" };
    const { container } = panel({ docSlug: "team", docPath: "notes/plan.md", pages: [{ path: "notes/plan.md", slug: "team", label: "plan" }] });
    await waitFor(() => { if (document.querySelector("[role=menu]")) fireEvent.click(container.querySelector("[data-doc-more]")!); openActs(container); expect(document.querySelector('[data-doc-act="delete"]')).toBeTruthy(); });
  });

  it("NOT in a shared workspace where they are a reader", async () => {
    roles = { team: "viewer" };
    const { container } = panel({ docSlug: "team", docPath: "notes/plan.md", pages: [{ path: "notes/plan.md", slug: "team", label: "plan" }] });
    await waitFor(() => expect(calls.some((c) => c.url.startsWith("/api/workspace/shared"))).toBe(true));
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('[data-doc-act="delete"]')).toBeNull();
  });

  it("NOT on `_global` for someone who is not its admin, and never on `_system`", async () => {
    for (const slug of ["_global", "_system"]) {
      resetOfferCache();
      const { container, unmount } = panel({ docSlug: slug, pages: [{ path: PAGE, slug, label: "x" }] });
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
      expect(container.querySelector('[data-doc-act="delete"]')).toBeNull();
      unmount();
    }
  });

  it("NOT on a workspace README — that is the workspace's front page", async () => {
    const { container } = panel({ docPath: "README.md", pages: [{ path: "README.md", label: "README" }] });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(container.querySelector('[data-doc-act="delete"]')).toBeNull();
  });
});

describe("the confirm, the delete and the undo", () => {
  it("names the page and the workspace, and its Delete button holds focus across re-renders", async () => {
    const { container } = panel();
    await waitFor(() => { if (document.querySelector("[role=menu]")) fireEvent.click(container.querySelector("[data-doc-more]")!); openActs(container); expect(document.querySelector('[data-doc-act="delete"]')).toBeTruthy(); });
    fireEvent.click(document.querySelector('[data-doc-act="delete"]')!);
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-label")).toBe("Delete so-vexam?");
    expect(dialog.textContent).toContain("It's removed from your personal workspace; you can restore it from History.");
    const confirm = document.querySelector("[data-page-delete-confirm]") as HTMLButtonElement;
    expect(document.activeElement).toBe(confirm);
    // the shell re-renders on every poll — a new `onClose` each time must not move focus to Cancel
    const { rerender } = render(<DeletePageDialog target={{ path: "a.md", title: "a", workspace: "w" }} onClose={() => {}} />);
    const second = document.querySelectorAll("[data-page-delete-confirm]")[1] as HTMLButtonElement;
    expect(document.activeElement).toBe(second);
    rerender(<DeletePageDialog target={{ path: "a.md", title: "a", workspace: "w" }} onClose={() => {}} />);
    expect(document.activeElement).toBe(second);
  });

  it("confirming removes the page through agent-api, drops its tabs and offers Undo with the commit", async () => {
    const removedEvents: unknown[] = [];
    const onRemoved = (e: Event) => removedEvents.push((e as CustomEvent).detail);
    window.addEventListener(PAGE_REMOVED_EVENT, onRemoved);
    const { container } = panel();
    await waitFor(() => { if (document.querySelector("[role=menu]")) fireEvent.click(container.querySelector("[data-doc-more]")!); openActs(container); expect(document.querySelector('[data-doc-act="delete"]')).toBeTruthy(); });
    fireEvent.click(document.querySelector('[data-doc-act="delete"]')!);
    await act(async () => { fireEvent.click(document.querySelector("[data-page-delete-confirm]")!); });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    window.removeEventListener(PAGE_REMOVED_EVENT, onRemoved);

    const rm = calls.find((c) => c.url === "/api/workspace/remove");
    expect(rm?.method).toBe("POST");
    expect(rm?.body).toEqual({ path: PAGE });
    expect(removedEvents).toEqual([{ path: PAGE, slug: undefined }]);
    expect(document.querySelector("[data-page-removed]")?.textContent).toContain("Deleted so-vexam.");

    await act(async () => { fireEvent.click(document.querySelector("[data-page-undo]")!); });
    const rs = calls.find((c) => c.url === "/api/workspace/restore");
    expect(rs?.method).toBe("POST");
    expect(rs?.body).toEqual({ path: PAGE, commit: "abc1234" });
    await waitFor(() => expect(removedNow()).toBeNull());
    expect(trees[""]).toContain(PAGE);
  });

  it("a refusal stays in the dialog, and nothing is said to be deleted", async () => {
    refuse = "only an org admin may edit _global";
    render(<><DeletePageDialog target={{ path: PAGE, title: "so-vexam", workspace: "w" }} onClose={() => {}} /><PageRemovedToast /></>);
    await act(async () => { fireEvent.click(document.querySelector("[data-page-delete-confirm]")!); });
    await waitFor(() => expect(document.querySelector("[data-page-delete-error]")?.textContent).toContain("Could not delete"));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(document.querySelector("[data-page-removed]")).toBeNull();
  });

  it("Undo goes away on its own; the page is then in History", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<><DeletePageDialog target={{ path: PAGE, title: "so-vexam", workspace: "w" }} onClose={() => {}} /><PageRemovedToast /></>);
    await act(async () => { fireEvent.click(document.querySelector("[data-page-delete-confirm]")!); });
    await waitFor(() => expect(document.querySelector("[data-page-removed]")).toBeTruthy());
    await act(async () => { vi.advanceTimersByTime(10_001); });
    expect(document.querySelector("[data-page-removed]")).toBeNull();
  });
});

describe("the tabs on a deleted page", () => {
  it("every tab on that page goes — preview and pin — and the home and a meeting's pages stay", () => {
    const pages: (Page & { permanent?: boolean })[] = [
      { path: "README.md", label: "home", desk: true },
      { path: PAGE, label: "so-vexam", pinned: true },
      { path: PAGE, slug: "team", label: "same path, other workspace" },
      { path: "notes/x.md", label: "x" },
      { path: PAGE, kind: "meeting", label: "a meeting row id that looks alike" },
    ];
    const next = dropRemovedPage(pages, PAGE, undefined);
    expect(next.map((p) => p.label)).toEqual(["home", "same path, other workspace", "x", "a meeting row id that looks alike"]);
  });
});

describe("a link to the deleted page", () => {
  it("resolves to nothing afterwards — which renders the dashed 'no page yet' chip", async () => {
    expect(await resolveDocRef({ wikilink: "so-vexam" }, { path: "README.md" })).toMatchObject({ path: PAGE });
    render(<DeletePageDialog target={{ path: PAGE, title: "so-vexam", workspace: "w" }} onClose={() => {}} />);
    await act(async () => { fireEvent.click(document.querySelector("[data-page-delete-confirm]")!); });
    await waitFor(() => expect(trees[""]).not.toContain(PAGE));
    expect(await resolveDocRef({ wikilink: "so-vexam" }, { path: "README.md" })).toBeUndefined();
  });
});

describe("the navigator's file row menu", () => {
  const nav = () => render(<Navigator onOpenTab={() => {}} onClose={() => {}}
    workspaces={[{ key: "desk", name: "Desk", kind: "desk" }, { key: "team", slug: "team", name: "Team", kind: "group" }]}
    trees={{ desk: [PAGE], team: ["notes/plan.md"] }} />);
  const openRow = async (key: string, path: string) => {
    fireEvent.click(document.querySelector(`[data-nav-ws="${key}"]`)!);
    // expand every folder down to the file, one level per pass
    const opened = new Set<string>();
    for (let i = 0; i < 6; i++) {
      for (const b of document.querySelectorAll<HTMLElement>("[data-nav-dir]")) {
        const k = b.getAttribute("data-nav-dir")!;
        if (!opened.has(k)) { opened.add(k); fireEvent.click(b); }
      }
    }
    await waitFor(() => expect(document.querySelector(`[data-nav-more="${key}|${path}"]`)).toBeTruthy());
    await act(async () => { fireEvent.click(document.querySelector(`[data-nav-more="${key}|${path}"]`)!); });
    await waitFor(() => expect(screen.getByRole("menu")).toBeTruthy());
  };

  it("offers Delete… on the desk, and it opens the same confirm", async () => {
    nav();
    await openRow("desk", PAGE);
    const del = screen.getByRole("menuitem", { name: /Delete…/ });
    fireEvent.click(del);
    expect(screen.getByRole("dialog").getAttribute("aria-label")).toBe("Delete so-vexam?");
    expect(screen.getByRole("dialog").textContent).toContain("your personal workspace");
  });

  it("offers no Delete… where the reader only reads", async () => {
    roles = { team: "viewer" };
    nav();
    await openRow("team", "notes/plan.md");
    expect(screen.getByRole("menuitem", { name: /Open in a tab/ })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: /Delete…/ })).toBeNull();
  });
});

describe("History's Restore finds the removal row", () => {
  it("reads the page off the subject, or off the files when the path was shortened", async () => {
    const { removedPathOf } = await import("../pageDelete");
    expect(removedPathOf({ msg: `7: ${PAGE} — removed`, files: [PAGE, "kg/INDEX.md"] })).toBe(PAGE);
    expect(removedPathOf({ msg: "7: …ong-name.md — removed", files: ["kg/INDEX.md", "kg/entities/x/a-long-name.md"] }))
      .toBe("kg/entities/x/a-long-name.md");
    expect(removedPathOf({ msg: `edit ${PAGE} (terminal page editor)`, files: [PAGE] })).toBeNull();
    expect(removedPathOf({ msg: `7: ${PAGE} — restored`, files: [PAGE] })).toBeNull();
  });
});

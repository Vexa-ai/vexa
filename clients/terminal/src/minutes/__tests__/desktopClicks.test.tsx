/** THE DESKTOP YOU CAN USE (#1783, founder on the live desktop 2026-10-15: "can't even click on those"
 *  and "this just does not exist").
 *
 *  Pinned here: the WHOLE workspace tile opens the workspace — its front page, the same act as
 *  choosing it in the Files navigator — by click and by Enter, and a cmd-/middle-click opens its
 *  `/w/<workspace>/README.md` link in a new tab; the tile's own controls (last call, Join, a
 *  person, "…") do their own thing and never open the tile. People are a stack of at most three
 *  with "+n". A company tile says "reader" once. Chats can be placed (`chat:<id>`) and are drawn in
 *  Agents; "Add to desktop" is in every menu that should have it, whether or not the desktop has
 *  been read yet; a file or a chat dragged from the sidebar onto its section is placed; the hidden
 *  workspaces line expands to the hidden ones, each with Show again. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const toasts: string[] = [];
vi.mock("../../ui-kit/primitives/Toast", () => ({
  toast: (message: string) => { toasts.push(message); },
  Toaster: () => null,
}));

import { setFeatureOverride } from "../../platform/features";
import { FOCUS_WORKSPACE_EVENT, OPEN_MEETING_EVENT, OPEN_SESSION_EVENT, OPEN_WORKSPACE_FILES_EVENT } from "../../platform";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Desktop, WorkspaceItem } from "../../platform/desktop";
import { composeDesktop, placedRefs } from "../../platform/desktop";
import { VIEW_NAVIGATE_EVENT } from "../roomView";
import { DesktopView } from "../desktop/DesktopView";
import { WorkspaceTile, BADGES_SHOWN } from "../desktop/DesktopTiles";
import { DESKTOP_DRAG_TYPE, useDesktopItems } from "../desktop/useDesktopItems";
import { resetDesktopStore, loadDesktop } from "../desktop/desktopApi";
import { Rail } from "../Rail";
import { PagesPanel } from "../PagesPanel";
import { ContextBar } from "../ContextBar";
import { Navigator } from "../Navigator";

const drag = { onDragStart: () => {}, onDragOver: () => {}, onDrop: () => {} };
const member = (subject: string, name: string, initials: string) => ({ subject, name, initials, role: "contributor", presence: null });
const tile = (over: Partial<WorkspaceItem> = {}): WorkspaceItem => ({
  kind: "workspace", ref: "workspace:alpha", workspace_kind: "shared", slug: "alpha", id: "alpha",
  name: "Alpha", access: "owner", write: true, members: [], last_activity: null, last_call: null, live_call: null,
  ...over,
});

function heard(name: string) {
  const got: unknown[] = [];
  const on = (e: Event) => got.push((e as CustomEvent).detail);
  window.addEventListener(name, on);
  return { got, off: () => window.removeEventListener(name, on) };
}

type Call = { url: string; body: unknown };
let calls: Call[] = [];
let desktopAnswer: () => Promise<Response>;
let scopeAnswer: unknown;
/** `/api/workspace/tree` by slug ("" = the person's own desk) */
let trees: Record<string, string[]>;

const sec = (id: string, over: Partial<Desktop["sections"][number]> = {}) => ({
  id, title: id[0].toUpperCase() + id.slice(1), hidden: id === "cards", collapsed: false, column: 1, span: 1, size: "normal", items: [], ...over,
});
function desktop(over: Partial<Desktop> = {}): Desktop {
  return {
    version: 5, undo_version: 4, saved: [], proposals: [], filters: [], hidden_workspaces: 0, presence_visibility: "workspace_members", cards_on: false,
    sections: [sec("workspaces"), sec("agents"), sec("files"), sec("cards")], ...over,
  };
}

beforeEach(() => {
  calls = []; toasts.length = 0;
  trees = { "": ["README.md", "notes/a.md"], alpha: ["README.md", "plan.md"], _global: ["README.md"], bare: ["plan.md"] };
  desktopAnswer = () => Promise.resolve(new Response(JSON.stringify(desktop()), { status: 200 }));
  scopeAnswer = { workspaces: [], hidden: 0, line: "", chat: { session: null, target: null, reads: [], own_reads: false }, defaults: { target: "", active: [] } };
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url: String(url), body });
    const u = String(url);
    if (u === "/api/desktop") return desktopAnswer();
    if (u.startsWith("/api/workspace/tree")) {
      const slug = new URL(u, "http://x").searchParams.get("slug") ?? "";
      return Promise.resolve(new Response(JSON.stringify({ files: trees[slug] ?? [] }), { status: 200 }));
    }
    if (u === "/api/workspace/file" && init?.method === "PUT") return Promise.resolve(new Response(JSON.stringify({ path: "README.md", written: true }), { status: 200 }));
    if (u.startsWith("/api/workspace/states")) return Promise.resolve(new Response(JSON.stringify(scopeAnswer), { status: 200 }));
    if (u === "/api/workspace/state") return Promise.resolve(new Response(JSON.stringify({ say: "Beta is back.", line: "" }), { status: 200 }));
    if (u.startsWith("/api/desktop/")) return Promise.resolve(new Response(JSON.stringify({ status: "applied", version: 6, undo_version: 5, summary: "Placed" }), { status: 200 }));
    return Promise.resolve(new Response("{}", { status: 200 }));
  }));
  setFeatureOverride("all");
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); resetDesktopStore(); setFeatureOverride("all"); vi.restoreAllMocks(); });

const posts = (path: string) => calls.filter((c) => c.url === path);

// ── A1 · the whole tile is a link ──────────────────────────────────────────────────────────────
describe("the workspace tile is a link", () => {
  it("a click anywhere on the tile opens the workspace's README.md as a page", async () => {
    const { container } = render(<WorkspaceTile drag={drag} w={tile({ last_activity: { line: "Sam changed Plan", at: 1 } })} />);
    const nav = heard(VIEW_NAVIGATE_EVENT);
    fireEvent.click(container.querySelector("[data-activity]")!);
    await waitFor(() => expect(nav.got).toEqual([{ workspace: "alpha", path: "README.md", label: "README" }]));
    nav.off();
  });

  it("a workspace with no README opens its file tree, where Create README is offered", async () => {
    const { container } = render(<WorkspaceTile drag={drag} w={tile({ slug: "bare", id: "bare", name: "Bare" })} />);
    const nav = heard(VIEW_NAVIGATE_EVENT);
    const files = heard(OPEN_WORKSPACE_FILES_EVENT);
    fireEvent.click(container.querySelector("[data-workspace-tile]")!);
    await waitFor(() => expect(files.got).toEqual([{ workspace: "bare", name: "Bare" }]));
    nav.off(); files.off();
    expect(nav.got).toEqual([]);
    cleanup();
    const onOpen = vi.fn();
    render(<PagesPanel pages={[]} docPath="" onOpen={onOpen} body={null} listing={{ slug: "bare", prefix: "", dirs: [], files: ["plan.md"] }} />);
    fireEvent.click(screen.getByRole("button", { name: "Create README" }));
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith({ path: "README.md", slug: "bare", label: "README" }));
    expect(calls.find((c) => c.url === "/api/workspace/file")?.body).toMatchObject({ path: "README.md", slug: "bare" });
  });

  it("the shell opens a workspace's file tree on that event", () => {
    const src = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");
    expect(src).toMatch(/addEventListener\(OPEN_WORKSPACE_FILES_EVENT/);
  });

  it("clicking around the desktop never starts a chat (no focus, no chat or session write)", async () => {
    render(<DesktopView desktop={desktop({ sections: [sec("workspaces", { items: [tile(), tile({ ref: "workspace:bare", slug: "bare", id: "bare", name: "Bare" })] }),
      sec("agents", { items: [{ kind: "chat", ref: "chat:pchat-a1", id: "pchat-a1", title: "Pricing prep", missing: false }] }), sec("files")] })} />);
    const focus = heard(FOCUS_WORKSPACE_EVENT);
    for (let i = 0; i < 3; i++) {
      for (const t of document.querySelectorAll("[data-workspace-tile]")) fireEvent.click(t);
      fireEvent.click(document.querySelector("[data-desk-section=workspaces]")!);
      fireEvent.click(document.querySelector("[data-desk-section=files]")!);
    }
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    focus.off();
    expect(focus.got).toEqual([]);
    const writes = calls.filter((c) => /^\/api\/(chat|sessions?)\b/.test(c.url));
    expect(writes).toEqual([]);
  });

  it("the person's own desk opens with no slug; a read-only workspace opens the same way", async () => {
    const { container, rerender } = render(<WorkspaceTile drag={drag} w={tile({ slug: "personal", id: "u1", name: "Personal", workspace_kind: "private" })} />);
    const nav = heard(VIEW_NAVIGATE_EVENT);
    fireEvent.click(container.querySelector("[data-workspace-tile]")!);
    await waitFor(() => expect(nav.got).toHaveLength(1));
    rerender(<WorkspaceTile drag={drag} w={tile({ slug: "_global", id: "_global", name: "Company", workspace_kind: "company", access: "reader", write: false })} />);
    fireEvent.click(container.querySelector("[data-workspace-tile]")!);
    await waitFor(() => expect(nav.got).toHaveLength(2));
    nav.off();
    expect(nav.got).toEqual([{ workspace: undefined, path: "README.md", label: "README" }, { workspace: "_global", path: "README.md", label: "README" }]);
  });

  it("is focusable, and Enter on it opens it", async () => {
    const { container } = render(<WorkspaceTile drag={drag} w={tile()} />);
    const t = container.querySelector<HTMLElement>("[data-workspace-tile]")!;
    expect(t.getAttribute("tabindex")).toBe("0");
    expect(t.getAttribute("role")).toBe("link");
    const nav = heard(VIEW_NAVIGATE_EVENT);
    t.focus();
    fireEvent.keyDown(t, { key: "Enter" });
    await waitFor(() => expect(nav.got).toHaveLength(1));
    nav.off();
  });

  it("cmd-click and middle-click open its /w/ link in a new tab, and do not navigate here", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const { container } = render(<WorkspaceTile drag={drag} w={tile()} />);
    const t = container.querySelector<HTMLElement>("[data-workspace-tile]")!;
    expect(t.getAttribute("data-href")).toBe("/w/alpha/README.md");
    const nav = heard(VIEW_NAVIGATE_EVENT);
    fireEvent.click(t, { metaKey: true });
    fireEvent(t, new MouseEvent("auxclick", { bubbles: true, button: 1 }));
    nav.off();
    expect(nav.got).toEqual([]);
    expect(open).toHaveBeenCalledTimes(2);
    expect(open.mock.calls[0][0]).toBe("/w/alpha/README.md");
    expect(open.mock.calls[0][1]).toBe("_blank");
  });

  it("the last call, Join, a person and \"…\" are their own targets — none of them opens the tile", () => {
    const { container } = render(<WorkspaceTile drag={drag} w={tile({
      members: [member("7", "Sam Example", "SE")],
      last_call: { meeting: "501", platform: "zoom", line: "Pricing review · Oct 14", at: 1 } })} />);
    const nav = heard(VIEW_NAVIGATE_EVENT);
    const meet = heard(OPEN_MEETING_EVENT);
    fireEvent.click(screen.getByRole("button", { name: /Last call · Pricing review/ }));
    fireEvent.keyDown(screen.getByRole("button", { name: /Last call · Pricing review/ }), { key: "Enter" });
    fireEvent.click(container.querySelector("[data-badge='7']")!);
    fireEvent.click(screen.getByRole("button", { name: "More for Alpha" }));
    nav.off(); meet.off();
    expect(meet.got).toEqual([{ ref: "501" }]);
    expect(nav.got).toEqual([]);
    expect(screen.getByRole("menu")).toBeTruthy();
    cleanup();
    render(<WorkspaceTile drag={drag} w={tile({ live_call: { meeting: "503", platform: "google_meet", line: "Standup", since: 1 } })} />);
    const nav2 = heard(VIEW_NAVIGATE_EVENT);
    fireEvent.click(screen.getByRole("button", { name: "Join" }));
    nav2.off();
    expect(nav2.got).toEqual([]);
  });

  it("a person is a button that shows their hover card", async () => {
    const { container } = render(<WorkspaceTile drag={drag} w={tile({ members: [member("7", "Sam Example", "SE")] })} />);
    const b = container.querySelector<HTMLElement>("[data-badge='7']")!;
    expect(b.tagName).toBe("BUTTON");
    fireEvent.mouseEnter(b);
    await waitFor(() => expect(document.querySelector("[data-hover-card]")?.textContent).toContain("Sam Example"));
  });
});

// ── A2 · the avatar stack ───────────────────────────────────────────────────────────────────────
describe("people on a tile", () => {
  it("are a stack of at most three, then +n; initials never longer than two letters", () => {
    const people = [member("1", "Maria Holt", "MH"), member("2", "Dmitri Mor", "DM"), member("3", "Ann Lee", "AL"),
      member("4", "Bo Kim", "BK"), member("5", "Cy Ray", "CYR")];
    const { container } = render(<WorkspaceTile drag={drag} w={tile({ members: people })} />);
    expect(BADGES_SHOWN).toBe(3);
    const badges = [...container.querySelectorAll("[data-badge]")];
    expect(badges).toHaveLength(3);
    for (const b of badges) expect((b.textContent ?? "").length).toBeLessThanOrEqual(2);
    expect(container.querySelector("[data-badges-more]")!.textContent).toBe("+2");
  });
});

// ── A4 · the company tile says reader once ─────────────────────────────────────────────────────
describe("a read-only tile", () => {
  it("the company tile says 'Company · reader' and no separate Read-only note", () => {
    const { container } = render(<WorkspaceTile drag={drag} w={tile({ slug: "_global", name: "Company", workspace_kind: "company", access: "reader", write: false })} />);
    expect(container.textContent).toContain("Company · reader");
    expect(container.querySelector("[data-readonly-note]")).toBeNull();
    expect(container.textContent).not.toContain("Read-only");
  });
});

// ── A3 · chats are agents too ──────────────────────────────────────────────────────────────────
describe("a chat on the desktop", () => {
  it("chat: refs go to agent-api and are drawn in Agents with the routines, in the person's order", () => {
    const desk = { version: 1, layout: { order: { agents: ["chat:pchat-a1", "agent:morning"] } }, placements: [
      { ref: "agent:morning" }, { ref: "chat:pchat-a1" }, { ref: "chat:pchat-gone" }], proposals: [], saved: [], undo_version: null };
    expect(placedRefs(desk)).toEqual(["agent:morning", "chat:pchat-a1", "chat:pchat-gone"]);
    const d = composeDesktop(desk, { presence_visibility: "off", workspaces: [], hidden_workspaces: 0, files: [],
      agents: [{ ref: "agent:morning", id: "morning", name: "Morning brief", missing: false }],
      chats: [{ ref: "chat:pchat-a1", id: "pchat-a1", title: "Pricing prep", last: "Active Oct 14", where: "Alpha", missing: false }] });
    const agents = d.sections.find((s) => s.id === "agents")!.items;
    expect(agents.map((i) => i.ref)).toEqual(["chat:pchat-a1", "agent:morning", "chat:pchat-gone"]);
    expect(agents.map((i) => i.kind)).toEqual(["chat", "agent", "chat"]);
    expect((agents[2] as { missing: boolean }).missing).toBe(true);
  });

  it("its tile says its title and last activity, and a press opens the chat", () => {
    render(<DesktopView desktop={desktop({ sections: [sec("agents", { items: [
      { kind: "chat", ref: "chat:pchat-a1", id: "pchat-a1", title: "Pricing prep", last: "Active Oct 14", where: "Alpha", missing: false },
      { kind: "chat", ref: "chat:pchat-x", id: "pchat-x", title: "", missing: true },
    ] })] })} />);
    const t = document.querySelector("[data-desk-tile='chat:pchat-a1']")!;
    expect(t.textContent).toContain("Pricing prep");
    expect(t.textContent).toContain("Active Oct 14");
    const open = heard(OPEN_SESSION_EVENT);
    fireEvent.click(screen.getByRole("button", { name: "Pricing prep" }));
    open.off();
    expect(open.got).toEqual([{ session: "pchat-a1" }]);
    expect(document.querySelector("[data-desk-tile='chat:pchat-x']")!.textContent).toContain("no longer available");
  });

  it("empty sections name only acts that exist", () => {
    render(<DesktopView desktop={desktop()} />);
    expect(document.querySelector("[data-desk-section=agents] .vx-desk-empty")!.textContent)
      .toBe("Pin a chat or routine here — choose Add to desktop in its …, or drag it here from the chat list.");
    expect(document.querySelector("[data-desk-section=files] .vx-desk-empty")!.textContent)
      .toBe("Pin a file here — choose Add to desktop in its …, or drag it here from Files.");
  });
});

// ── B1 · Add to desktop is in every menu ───────────────────────────────────────────────────────
function Probe({ target }: { target: Parameters<ReturnType<typeof useDesktopItems>>[0] }) {
  const items = useDesktopItems()(target);
  return <div>{items.map((i) => <button key={i.id} onClick={() => void i.onSelect()}>{i.label}</button>)}</div>;
}

describe("Add to desktop", () => {
  it("is offered while the desktop has not been read yet (a read that never answers)", async () => {
    desktopAnswer = () => new Promise(() => {});
    render(<Probe target={{ kind: "chat", id: "pchat-a1" }} />);
    expect(await screen.findByRole("button", { name: "Add to desktop" })).toBeTruthy();
  });

  it("for a chat places chat:<id>", async () => {
    render(<Probe target={{ kind: "chat", id: "pchat-a1" }} />);
    await act(async () => { await loadDesktop(); });
    fireEvent.click(await screen.findByRole("button", { name: "Add to desktop" }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toMatchObject({ ops: [{ op: "place", ref: "chat:pchat-a1" }] });
  });

  it("the chat list: a chat row's … and a routine row's …", async () => {
    const rows = [{ key: "chat:a", chatId: "pchat-a", label: "Calendar setup", whenLabel: "Today" },
      { key: "chat:r", chatId: "pchat-r", label: "Morning brief", whenLabel: "Today", routine: { flow: "morning" } }] as never;
    render(<Rail rows={rows} hidden={0} all onAll={() => {}} selKey={null} onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for Calendar setup" }));
    expect(await screen.findByRole("menuitem", { name: /Add to desktop/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("menuitem", { name: /Add to desktop/ }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toMatchObject({ ops: [{ op: "place", ref: "chat:pchat-a" }] });
    fireEvent.click(screen.getByRole("button", { name: "Actions for Morning brief" }));
    expect(await screen.findByRole("menuitem", { name: /Add to desktop/ })).toBeTruthy();
  });

  it("the page's … in the right panel", async () => {
    const { container } = render(<PagesPanel pages={[{ path: "plan.md", slug: "alpha", label: "plan" }]} docPath="plan.md" docSlug="alpha" onOpen={() => {}} body={"# Plan\n\nx"} />);
    fireEvent.click(container.querySelector("[data-doc-more]")!);
    expect(await screen.findByRole("menuitem", { name: /Add to desktop/ })).toBeTruthy();
  });

  it("a row in the chat's files list has its own … with it", async () => {
    render(<PagesPanel pages={[]} docPath="" onOpen={() => {}} body={null}
      artifacts={[{ key: "a1", kind: "wrote", title: "Sponsor plan", page: { path: "plans/sponsor.md", slug: "alpha", label: "Sponsor plan" } }]} />);
    fireEvent.click(await screen.findByRole("button", { name: "More for Sponsor plan" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /Add to desktop/ }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toMatchObject({ ops: [{ op: "place", ref: "file:alpha/plans/sponsor.md" }] });
  });

  it("the chat header's …", async () => {
    const sel = { kind: "chat", chatId: "pchat-h", label: "Weekly ops", workspaces: ["personal"] } as never;
    render(<ContextBar sel={sel} flavor="chat" memberships={[]} onAddWorkspace={() => {}} onRemoveWorkspace={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "More for this chat" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /Add to desktop/ }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toMatchObject({ ops: [{ op: "place", ref: "chat:pchat-h" }] });
  });

  it("the file tree's …", async () => {
    render(<Navigator onOpenTab={() => {}} onClose={() => {}} workspaces={[{ key: "team", slug: "team", name: "Team", kind: "group" }]} trees={{ team: ["plan.md"] }} />);
    fireEvent.click(document.querySelector("[data-nav-ws='team']")!);
    await waitFor(() => expect(document.querySelector("[data-nav-more='team|plan.md']")).toBeTruthy());
    await act(async () => { fireEvent.click(document.querySelector("[data-nav-more='team|plan.md']")!); });
    expect(await screen.findByRole("menuitem", { name: /Add to desktop/ })).toBeTruthy();
  });

  it("none of them with the desktop switched off", async () => {
    setFeatureOverride({ desktop: false });
    const sel = { kind: "chat", chatId: "pchat-h", label: "Weekly ops", workspaces: ["personal"] } as never;
    render(<ContextBar sel={sel} flavor="chat" memberships={[]} onAddWorkspace={() => {}} onRemoveWorkspace={() => {}} />);
    expect(screen.queryByRole("button", { name: "More for this chat" })).toBeNull();
  });
});

// ── B2 · drag from the sidebar ─────────────────────────────────────────────────────────────────
const dt = () => {
  const store: Record<string, string> = {};
  return { setData: (k: string, v: string) => { store[k] = v; }, getData: (k: string) => store[k] ?? "", effectAllowed: "move", dropEffect: "move",
    get types() { return Object.keys(store); } };
};

describe("dragging onto the desktop", () => {
  it("a chat row carries itself; dropped on Agents it is placed", async () => {
    await act(async () => { await loadDesktop(); });
    const rows = [{ key: "chat:a", chatId: "pchat-a", label: "Calendar setup", whenLabel: "Today" }] as never;
    render(<><Rail rows={rows} hidden={0} all onAll={() => {}} selKey={null} onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} />
      <DesktopView desktop={desktop()} /></>);
    const data = dt();
    const row = document.querySelector("[data-row-key='chat:a']")!;
    expect(row.getAttribute("draggable")).toBe("true");
    fireEvent.dragStart(row, { dataTransfer: data });
    expect(JSON.parse(data.getData(DESKTOP_DRAG_TYPE))).toEqual({ kind: "chat", id: "pchat-a" });
    const agents = document.querySelector("[data-desk-section=agents]")!;
    fireEvent.dragOver(agents, { dataTransfer: data });
    fireEvent.drop(agents, { dataTransfer: data });
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toMatchObject({ ops: [{ op: "place", ref: "chat:pchat-a" }] });
  });

  it("a file from the file tree, dropped on Files, is placed; dropped on Agents it is not", async () => {
    await act(async () => { await loadDesktop(); });
    render(<><Navigator onOpenTab={() => {}} onClose={() => {}} workspaces={[{ key: "team", slug: "team", name: "Team", kind: "group" }]} trees={{ team: ["plan.md"] }} />
      <DesktopView desktop={desktop()} /></>);
    fireEvent.click(document.querySelector("[data-nav-ws='team']")!);
    await waitFor(() => expect(document.querySelector("[data-nav-file='team|plan.md']")).toBeTruthy());
    const data = dt();
    fireEvent.dragStart(document.querySelector("[data-nav-file='team|plan.md']")!, { dataTransfer: data });
    fireEvent.drop(document.querySelector("[data-desk-section=agents]")!, { dataTransfer: data });
    fireEvent.drop(document.querySelector("[data-desk-section=files]")!, { dataTransfer: data });
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toMatchObject({ ops: [{ op: "place", ref: "file:team/plan.md" }] });
  });
});

// ── B3 · hidden workspaces come back ───────────────────────────────────────────────────────────
describe("hidden workspaces", () => {
  it("the line expands to the hidden ones, each with Show again, which unhides it", async () => {
    scopeAnswer = { workspaces: [
      { slug: "alpha", name: "Alpha", kind: "shared", state: "active", reads: true, target: false },
      { slug: "beta-1", name: "beta-1", display: "Beta", kind: "shared", state: "hidden", reads: false, target: false },
    ], hidden: 1, line: "", chat: { session: null, target: null, reads: [], own_reads: false }, defaults: { target: "", active: [] } };
    render(<DesktopView desktop={desktop({ hidden_workspaces: 1 })} />);
    const line = screen.getByRole("button", { name: /Hidden workspaces \(1\)/ });
    expect(line.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(line);
    const row = await waitFor(() => { const r = document.querySelector("[data-hidden-workspace='beta-1']"); if (!r) throw new Error("no row"); return r; });
    expect(row.textContent).toContain("Beta");
    expect(document.querySelector("[data-hidden-workspace='alpha']")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show Beta again" }));
    await waitFor(() => expect(posts("/api/workspace/state")).toHaveLength(1));
    expect(posts("/api/workspace/state")[0].body).toMatchObject({ workspaces: ["beta-1"], state: "active", scope: "default" });
  });
});

// ── a chat with no messages is never a Recent row ──────────────────────────────────────────────
import { dropEmptyUnlisted } from "../chats";
describe("empty chats in the chat list", () => {
  const NOW = 1_800_000_000_000;
  const chat = (id: string, over: Record<string, unknown> = {}) => ({ id, label: "New chat", createdAt: NOW - 3_600_000, lastActivityAt: 0, ...over }) as never;
  it("a placeholder chat the server no longer lists goes; a named one, a meeting's, an ask's and a fresh one stay", () => {
    const local = [chat("pchat-empty"), chat("pchat-named", { label: "Pricing prep" }), chat("meet-41", { meeting: "41" }),
      chat("pchat-ask", { scaffold: { kind: "ask", id: "a1" } }), chat("pchat-fresh", { createdAt: NOW - 10_000 }), chat("pchat-listed")];
    const server = [chat("pchat-listed")];
    expect(dropEmptyUnlisted(local, server, NOW).map((c: { id: string }) => c.id))
      .toEqual(["pchat-named", "meet-41", "pchat-ask", "pchat-fresh", "pchat-listed"]);
  });
  it("the shell applies it wherever it reads the full chat list", () => {
    const src = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");
    expect((src.match(/dropEmptyUnlisted\(/g) ?? []).length).toBeGreaterThanOrEqual(3);
  });
});

// ── lab follow-ups (2026-10-15): the README's path line, the tree's name, Delete last ──────────
describe("what a tile opens reads like a page", () => {
  it("a workspace README shows the path line — the workspace, then README.md — like any page", async () => {
    render(<PagesPanel pages={[{ path: "README.md", slug: "alpha", label: "README" }]} docPath="README.md" docSlug="alpha" onOpen={() => {}} body={"# Alpha\n\nHello."} />);
    const line = await waitFor(() => { const l = document.querySelector("[data-page-path]"); if (!l) throw new Error("no path line"); return l; });
    expect(line.textContent).toContain("README.md");
    expect(line.querySelector("[data-path-seg='']")).toBeTruthy();   // the workspace segment opens its root
  });

  it("a workspace's file tree is headed with its name, never its slug", () => {
    render(<PagesPanel pages={[]} docPath="" onOpen={() => {}} body={null} listing={{ slug: "garden", name: "Garden plot", prefix: "", dirs: [], files: ["plan.md"] }} />);
    const crumb = document.querySelector("[data-crumb]")!;
    expect(crumb.textContent).toContain("Garden plot");
    expect(crumb.textContent).not.toContain("garden");
  });

  it("the shell passes the name the tile sent to the listing", () => {
    const src = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");
    const handler = src.slice(src.indexOf("const onFiles"), src.indexOf("addEventListener(OPEN_WORKSPACE_FILES_EVENT"));
    expect(handler).toMatch(/navigate\(slug, "", [^)]*name/);
  });
});

describe("a chat row's …", () => {
  it("Add to desktop sits above Delete…, and Delete… is last", async () => {
    const rows = [{ key: "chat:a", chatId: "pchat-a", label: "Calendar setup", whenLabel: "Today" }] as never;
    render(<Rail rows={rows} hidden={0} all onAll={() => {}} selKey={null} onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}} onPin={() => {}} onHide={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for Calendar setup" }));
    await screen.findByRole("menuitem", { name: /Add to desktop/ });
    const labels = screen.getAllByRole("menuitem").map((m) => m.textContent ?? "");
    const add = labels.findIndex((l) => /Add to desktop/.test(l));
    const del = labels.findIndex((l) => /Delete…/.test(l));
    expect(add).toBeGreaterThanOrEqual(0);
    expect(del).toBe(labels.length - 1);
    expect(add).toBeLessThan(del);
  });
});

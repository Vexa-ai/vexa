/** THE DESKTOP ON THE HOME (#1783, desktop ADR §2.3a, D1; UI decisions 15b, 15e, 15c, 15a).
 *
 *  Pinned here: behind the `desktop` switch only (off → the current home, untouched; crm refusing →
 *  the current home); sections drawn in the layout's order with Cards never drawn in D1; a drag on a
 *  section header or a tile writes the SAME ops the agent writes (`section_order`, `move`) with
 *  `base_version`, and a `desk_changed` reloads and retries once; every applied change offers Undo;
 *  a pending proposal is a quiet "Vexa suggests" row with Apply / Not now; an active filter is a chip
 *  that clears it; a placement that no longer resolves says so with Remove; a hidden section comes
 *  back from the desktop's "…"; "Add to desktop" / "Remove from desktop" in the routine and file
 *  menus write `place` / `remove`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const toasts: { message: string; undo?: () => void }[] = [];
vi.mock("../../ui-kit/primitives/Toast", () => ({
  toast: (message: string, opts?: { undo?: () => void }) => { toasts.push({ message, undo: opts?.undo }); },
  Toaster: () => null,
}));

import { setFeatureOverride } from "../../platform/features";
import { FOCUS_WORKSPACE_EVENT, OPEN_SESSION_EVENT } from "../../platform";
import type { Desktop } from "../../platform/desktop";
import { DesktopHome } from "../desktop/DesktopHome";
import { resetDesktopStore } from "../desktop/desktopApi";
import { useDesktopItems } from "../desktop/useDesktopItems";
import { VIEW_NAVIGATE_EVENT } from "../roomView";

const sec = (id: string, over: Partial<Desktop["sections"][number]> = {}) => ({
  id, title: id[0].toUpperCase() + id.slice(1), hidden: id === "cards", collapsed: false, column: 1, span: 1, size: "normal", items: [], ...over,
});

function desktop(over: Partial<Desktop> = {}): Desktop {
  return {
    version: 5, undo_version: 4, saved: [], proposals: [], filters: [], hidden_workspaces: 0, presence_visibility: "workspace_members", cards_on: false,
    sections: [
      sec("workspaces", { items: [
        { kind: "workspace", ref: "workspace:alpha", slug: "alpha", id: "w1", name: "Alpha project", workspace_kind: "shared", access: "owner", write: true,
          members: [
            { subject: "7", name: "Sam Example", initials: "SE", role: "contributor", presence: "now" },
            { subject: "8", name: "Kim Sample", initials: "KS", role: "reader", presence: null },
          ],
          last_activity: { line: "Sam changed Plan", at: 1 }, last_call: { meeting: "11", line: "Weekly sync · Oct 14", at: 1 },
          live_call: { session: "pchat-live1", meeting: "12", people: 2, since: 1 } },
        { kind: "workspace", ref: "workspace:beta", slug: "beta", id: "w2", name: "Beta notes", workspace_kind: "company", access: "reader", write: false, members: [] },
      ] }),
      sec("agents", { items: [
        { kind: "agent", ref: "agent:morning", id: "morning", name: "Morning brief", what: "Summarises the day", status: "idle", last: "today 8:00", next: "tomorrow 8:00", where: "Alpha project", thread: "pchat-brief1", missing: false },
        { kind: "agent", ref: "agent:old", id: "old", name: "", missing: true },
      ] }),
      sec("files", { items: [
        { kind: "file", ref: "file:alpha/plan.md", slug: "alpha", path: "plan.md", title: "Plan", where: "Alpha project", changed: "yesterday", missing: false },
        { kind: "file", ref: "file:alpha/notes.md", slug: "alpha", path: "notes.md", title: "Notes", where: "Alpha project", missing: false },
      ] }),
      sec("cards"),
    ],
    ...over,
  };
}

type Call = { url: string; body: unknown };
let calls: Call[] = [];
let answers: Record<string, Array<{ status: number; body: unknown }>> = {};
let current: Desktop;

function route(url: string, init?: RequestInit) {
  const body = init?.body ? JSON.parse(String(init.body)) : undefined;
  calls.push({ url, body });
  const q = answers[url];
  if (q && q.length) { const a = q.shift()!; return Promise.resolve(new Response(JSON.stringify(a.body), { status: a.status })); }
  if (url.startsWith("/api/workspace/tree")) return Promise.resolve(new Response(JSON.stringify({ files: ["README.md"] }), { status: 200 }));
  if (url === "/api/desktop") return Promise.resolve(new Response(JSON.stringify(current), { status: 200 }));
  return Promise.resolve(new Response(JSON.stringify({ status: "applied", version: current.version + 1, undo_version: current.version, summary: "Arranged" }), { status: 200 }));
}

const posts = (path: string) => calls.filter((c) => c.url === path);

beforeEach(() => {
  calls = []; answers = {}; toasts.length = 0; current = desktop();
  vi.stubGlobal("fetch", vi.fn(route));
  setFeatureOverride("all");
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); resetDesktopStore(); setFeatureOverride("all"); });

const home = () => render(<DesktopHome active greeting={{ title: "Good morning" }} fallback={<div data-testid="old-home">old home</div>} />);
const loaded = async () => { home(); await screen.findByRole("region", { name: "Workspaces" }); };

describe("behind the desktop switch", () => {
  it("off: the current home, untouched, and no desktop read", async () => {
    setFeatureOverride({ desktop: false });
    home();
    expect(screen.getByTestId("old-home")).toBeTruthy();
    await act(async () => { await Promise.resolve(); });
    expect(posts("/api/desktop")).toHaveLength(0);
    expect(document.querySelector("[data-desktop]")).toBeNull();
  });

  it("on, but crm refuses (feature off there): the current home", async () => {
    answers["/api/desktop"] = [{ status: 403, body: { status: "refused", reason: "feature_disabled" } }];
    home();
    expect(await screen.findByTestId("old-home")).toBeTruthy();
  });

  it("on: what the home shows besides the desktop (the People strip) stays, under the starters", async () => {
    render(<DesktopHome active greeting={{ title: "Hi" }} extra={<div data-testid="people-strip">people</div>} fallback={<div data-testid="old-home">old</div>} />);
    expect(await screen.findByTestId("people-strip")).toBeTruthy();
    expect(screen.queryByTestId("old-home")).toBeNull();
  });

  it("not the new chat in front: the current home", () => {
    render(<DesktopHome active={false} fallback={<div data-testid="old-home">old</div>} />);
    expect(screen.getByTestId("old-home")).toBeTruthy();
  });
});

describe("the sections", () => {
  it("are drawn in the layout's order with a header each; Cards is never drawn in D1", async () => {
    current = desktop({ sections: [desktop().sections[2], desktop().sections[0], desktop().sections[1], desktop().sections[3]] });
    await loaded();
    const heads = [...document.querySelectorAll("[data-desk-section]")].map((s) => s.getAttribute("data-desk-section"));
    expect(heads).toEqual(["files", "workspaces", "agents"]);
    expect(screen.getByText("Good morning")).toBeTruthy();
    expect(document.querySelector("[data-desk-section=cards]")).toBeNull();
  });

  it("a workspace tile: kind · access, activity, last call, live call with Join, presence only for now/today, read-only note", async () => {
    await loaded();
    const alpha = document.querySelector("[data-workspace-tile=alpha]")!;
    expect(alpha.textContent).toContain("Shared · owner");
    expect(alpha.textContent).toContain("Sam changed Plan");
    expect(alpha.textContent).toContain("Call now · 2 people");
    expect(alpha.querySelectorAll("[data-presence]").length).toBe(1);
    expect(alpha.querySelector("[data-presence]")!.getAttribute("data-presence")).toBe("now");
    const opened: unknown[] = [];
    const onOpen = (e: Event) => opened.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_SESSION_EVENT, onOpen);
    fireEvent.click(screen.getByRole("button", { name: "Join" }));
    window.removeEventListener(OPEN_SESSION_EVENT, onOpen);
    expect(opened).toEqual([{ session: "pchat-live1" }]);
    const beta = document.querySelector("[data-workspace-tile=beta]")!;
    // read-only is said once, in the kind line (founder 2026-10-15) — no separate note
    expect(beta.textContent).toContain("Company · reader");
    expect(beta.querySelector("[data-readonly-note]")).toBeNull();
  });

  it("clicking a workspace opens its README (never retargets the chat in front)", async () => {
    await loaded();
    const seen: unknown[] = [];
    const nav: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    const onNav = (e: Event) => nav.push((e as CustomEvent).detail);
    window.addEventListener(FOCUS_WORKSPACE_EVENT, on);
    window.addEventListener(VIEW_NAVIGATE_EVENT, onNav);
    fireEvent.click(document.querySelector("[data-workspace-tile=alpha]")!);
    await waitFor(() => expect(nav).toEqual([{ workspace: "alpha", path: "README.md", label: "README" }]));
    window.removeEventListener(FOCUS_WORKSPACE_EVENT, on);
    window.removeEventListener(VIEW_NAVIGATE_EVENT, onNav);
    expect(seen).toEqual([]);
  });

  it("an agent tile opens its chat; a missing one says so and offers Remove", async () => {
    await loaded();
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_SESSION_EVENT, on);
    fireEvent.click(screen.getByRole("button", { name: "Morning brief" }));
    window.removeEventListener(OPEN_SESSION_EVENT, on);
    expect(seen).toEqual([{ session: "pchat-brief1" }]);
    const gone = document.querySelector("[data-desk-tile='agent:old']")!;
    expect(gone.textContent).toContain("no longer available");
    fireEvent.click(gone.querySelector("button")!);
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toEqual({ ops: [{ op: "remove", ref: "agent:old" }], base_version: 5 });
  });

  it("collapse writes the collapse op; a hidden section comes back from the desktop's …", async () => {
    current = desktop({ sections: desktop().sections.map((s) => (s.id === "files" ? { ...s, hidden: true } : s)) });
    await loaded();
    expect(document.querySelector("[data-desk-section=files]")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Collapse Agents" }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toEqual({ ops: [{ op: "collapse", section: "agents" }], base_version: 5 });
    fireEvent.click(screen.getByRole("button", { name: "More for the desktop" }));
    expect(screen.queryByRole("menuitem", { name: /Show Cards/ })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: /Show Files/ }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(2));
    expect(posts("/api/desktop/arrange")[1].body).toMatchObject({ ops: [{ op: "show", section: "files" }] });
  });
});

const dt = () => {
  const store: Record<string, string> = {};
  return { setData: (k: string, v: string) => { store[k] = v; }, getData: (k: string) => store[k] ?? "", effectAllowed: "move", dropEffect: "move", types: [] as string[] };
};

describe("arranging by hand writes the agent's ops", () => {
  it("dragging a section header writes section_order with base_version", async () => {
    await loaded();
    const data = dt();
    const files = document.querySelector("[data-desk-section=files] [data-desk-handle]")!;
    const workspaces = document.querySelector("[data-desk-section=workspaces]")!;
    fireEvent.dragStart(files, { dataTransfer: data });
    fireEvent.dragOver(workspaces, { dataTransfer: data });
    fireEvent.drop(workspaces, { dataTransfer: data });
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toEqual({
      ops: [{ op: "section_order", sections: ["files", "workspaces", "agents", "cards"] }], base_version: 5,
    });
  });

  it("dragging a tile onto another in its section writes move before it", async () => {
    await loaded();
    const data = dt();
    const notes = document.querySelector("[data-desk-tile='file:alpha/notes.md']")!;
    const plan = document.querySelector("[data-desk-tile='file:alpha/plan.md']")!;
    fireEvent.dragStart(notes, { dataTransfer: data });
    fireEvent.dragOver(plan, { dataTransfer: data });
    fireEvent.drop(plan, { dataTransfer: data });
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toEqual({
      ops: [{ op: "move", id: "file:alpha/notes.md", before: "file:alpha/plan.md" }], base_version: 5,
    });
  });

  it("desk_changed: reloads the desktop and retries once on the new version", async () => {
    await loaded();
    answers["/api/desktop/arrange"] = [{ status: 409, body: { status: "refused", reason: "desk_changed", version: 7 } }];
    current = desktop({ version: 7, undo_version: 6 });
    fireEvent.click(screen.getByRole("button", { name: "Collapse Files" }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(2));
    expect((posts("/api/desktop/arrange")[0].body as { base_version: number }).base_version).toBe(5);
    expect((posts("/api/desktop/arrange")[1].body as { base_version: number }).base_version).toBe(7);
  });

  it("every applied change offers Undo, which restores undo_version", async () => {
    await loaded();
    answers["/api/desktop/arrange"] = [{ status: 200, body: { status: "applied", version: 6, undo_version: 5, summary: "Collapsed Files" } }];
    current = desktop({ version: 6, undo_version: 5 });   // the server, after the change
    fireEvent.click(screen.getByRole("button", { name: "Collapse Files" }));
    await waitFor(() => expect(toasts.length).toBe(1));
    expect(toasts[0].message).toBe("Collapsed Files");
    expect((posts("/api/desktop/arrange")[0].body as { base_version: number }).base_version).toBe(5);
    await waitFor(() => expect(posts("/api/desktop").length).toBeGreaterThanOrEqual(2));
    await act(async () => { toasts[0].undo!(); });
    await waitFor(() => expect(posts("/api/desktop/undo")).toHaveLength(1));
    expect(posts("/api/desktop/undo")[0].body).toEqual({ version: 5, base_version: 6 });
  });
});

describe("what the agent proposed, and filters", () => {
  it("a pending proposal is a quiet row: Apply applies it, Not now dismisses it", async () => {
    current = desktop({ proposals: [{ id: "p1", ops: [], summary: "Move Agents above Workspaces", reason: "you open agents first" }] });
    await loaded();
    const row = document.querySelector("[data-desk-proposal=p1]")!;
    expect(row.textContent).toContain("Vexa suggests: Move Agents above Workspaces");
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(posts("/api/desktop/proposal")).toHaveLength(1));
    expect(posts("/api/desktop/proposal")[0].body).toEqual({ proposal_id: "p1", apply: true, base_version: 5 });
    await waitFor(() => expect(toasts.some((t) => !!t.undo)).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await waitFor(() => expect(posts("/api/desktop/proposal")).toHaveLength(2));
    expect(posts("/api/desktop/proposal")[1].body).toMatchObject({ proposal_id: "p1", apply: false });
  });

  it("an active filter shows as a chip whose × clears it", async () => {
    current = desktop({ filters: [{ section: "files", where: "title contains \"plan\"" }] });
    await loaded();
    expect(document.querySelector("[data-desk-filter=files]")!.textContent).toContain("Filtered: title contains \"plan\"");
    fireEvent.click(screen.getByRole("button", { name: /Clear filter/ }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toEqual({ ops: [{ op: "filter", section: "files", where: null }], base_version: 5 });
  });
});

function MenuProbe({ target }: { target: Parameters<ReturnType<typeof useDesktopItems>>[0] }) {
  const items = useDesktopItems()(target);
  return <div>{items.map((i) => <button key={i.id} onClick={() => void i.onSelect()}>{i.label}</button>)}</div>;
}

describe("Add to desktop / Remove from desktop in the routine and file menus", () => {
  it("offers Add for an unplaced routine and writes place; Remove for a placed one", async () => {
    current = desktop();
    render(<><MenuProbe target={{ kind: "agent", id: "evening" }} /><MenuProbe target={{ kind: "agent", id: "morning" }} /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Add to desktop" }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toEqual({ ops: [{ op: "place", ref: "agent:evening" }], base_version: 5 });
    fireEvent.click(screen.getByRole("button", { name: "Remove from desktop" }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(2));
    expect(posts("/api/desktop/arrange")[1].body).toMatchObject({ ops: [{ op: "remove", ref: "agent:morning" }] });
  });

  it("a file in a workspace places file:<slug>/<path>; nothing is offered with the switch off", async () => {
    render(<MenuProbe target={{ kind: "file", slug: "alpha", path: "docs/new.md" }} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add to desktop" }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toMatchObject({ ops: [{ op: "place", ref: "file:alpha/docs/new.md" }] });
    cleanup();
    setFeatureOverride({ desktop: false });
    render(<MenuProbe target={{ kind: "file", slug: "alpha", path: "docs/new.md" }} />);
    expect(screen.queryByRole("button", { name: "Add to desktop" })).toBeNull();
  });
});

/** HOME2 (#1783, R3; approved mocks proto-on-product 96a5f50a4 + 382a319b5; decision E1).
 *
 *  Pinned here: a workspace on the home is its STATUS CARD — what needs you (at most two, with their
 *  act), what is working there, or the calm line; a click aims the home composer there (never a
 *  read-only workspace) and opens the workspace PAGE in the right panel, never the centre (7c). The
 *  Now strip names only workspaces on the home. The rich page renderer draws a reference the reader
 *  may not see as nothing (a room as "a private room") — never its name or its label; an action
 *  outside the allow-list is only its words; acts beyond the screen are confirmed; HTML is inert;
 *  plain tables search and sort. The workspace page hides empty sections. Off, nothing changes. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../../ui-kit/primitives/Toast", () => ({ toast: () => undefined, Toaster: () => null }));

import { setFeatureOverride } from "../../platform/features";
import { ASK_CHAT_EVENT, HOME_TARGET_EVENT, OPEN_SESSION_EVENT, NEW_CHAT_AT_EVENT, NEW_ROOM_EVENT } from "../../platform/events";
import type { Desktop, WorkspaceItem } from "../../platform/desktop";
import { VIEW_NAVIGATE_EVENT } from "../roomView";
import { DesktopView } from "../desktop/DesktopView";
import { resetDesktopStore } from "../desktop/desktopApi";
import { StatusCard } from "../desktop/home2/StatusCard";
import { NowStrip } from "../desktop/home2/NowStrip";
import { RichPage } from "../desktop/home2/RichPage";
import { WorkspacePage, humanStatus } from "../desktop/home2/WorkspacePage";
import { resetAttention, type Attention } from "../desktop/home2/attentionApi";
import { resetHomeTarget } from "../desktop/home2/HomeTarget";

const drag = { onDragStart: () => {}, onDragOver: () => {}, onDrop: () => {} };
const tile = (over: Partial<WorkspaceItem> = {}): WorkspaceItem => ({
  kind: "workspace", ref: "workspace:alpha", workspace_kind: "shared", slug: "alpha", id: "alpha",
  name: "Alpha", access: "owner", write: true, members: [], last_activity: null, last_call: null, live_call: null, ...over,
});
const sec = (id: string, over: Partial<Desktop["sections"][number]> = {}) => ({ id, title: id, hidden: id === "cards", collapsed: false, column: 1, span: 1, size: "normal", items: [], ...over });
const desktop = (items: WorkspaceItem[]): Desktop => ({
  version: 1, undo_version: null, saved: [], proposals: [], filters: [], hidden_workspaces: 0, presence_visibility: "off", cards_on: false,
  sections: [sec("workspaces", { items }), sec("agents"), sec("files"), sec("cards")],
});
const need = (over: Partial<Attention["needs"][number]> = {}) => ({
  id: "proposal:p1", kind: "proposal" as const, workspace: "alpha", text: "Move Search to Q2", at: 1,
  act: { label: "Accept", kind: "accept-proposal" as const, proposal: "p1" }, ...over,
});

function heard(name: string) {
  const got: unknown[] = [];
  const on = (e: Event) => got.push((e as CustomEvent).detail);
  window.addEventListener(name, on);
  return { got, off: () => window.removeEventListener(name, on) };
}

let attention: Attention;
let one: Record<string, unknown>;
let refs: Record<string, unknown>;
let calls: { url: string; body: unknown }[];
let files: Record<string, string>;

beforeEach(() => {
  calls = [];
  files = {};
  attention = { generated_at: 1, needs: [], workspaces: {} };
  one = {};
  refs = {};
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url: String(url), body });
    const u = String(url);
    const ok = (x: unknown) => Promise.resolve(new Response(JSON.stringify(x), { status: 200 }));
    if (u === "/api/desktop/attention") return ok(body?.workspace ? { generated_at: 1, needs: [], workspaces: { [body.workspace]: one } } : attention);
    if (u === "/api/desktop/refs") return ok({ refs });
    if (u.startsWith("/api/workspace/tree")) return ok({ files: Object.keys(files) });
    if (u.startsWith("/api/workspace/file")) {
      const p = new URL(u, "http://x").searchParams.get("path") ?? "";
      return p in files ? ok({ content: files[p] }) : Promise.resolve(new Response("{}", { status: 404 }));
    }
    if (u.includes("/git/history")) return ok({ slug: "alpha", branch: "main", path: null, limit: 20, commits: [] });
    return ok({});
  }));
  setFeatureOverride({ home2: true });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); resetDesktopStore(); resetAttention(); resetHomeTarget(); setFeatureOverride("all"); });

describe("the status card", () => {
  it("shows at most two needs with their act, the working chips, and the count", async () => {
    attention.workspaces.alpha = {
      needs: [need(), need({ id: "mention:m1", kind: "mention", text: "Mays asked you", act: { label: "Open", kind: "open-session", session: "s1" } }), need({ id: "failed:r1", kind: "failed", text: "Sweep failed" })],
      working: [{ kind: "routine", label: "Digest", state: "ok", when: "07:00", at: 1, ref: "agent:flow:digest" }], rooms_active: 2, live_call: null, last: null,
    };
    render(<StatusCard w={tile()} drag={drag} />);
    await screen.findByText("Move Search to Q2");
    // ONE status line on a card (founder 2026-10-16: boxes in a grid)
    expect(screen.queryByText("Mays asked you")).toBeNull();
    expect(screen.queryByText("Sweep failed")).toBeNull();
    expect(document.querySelector("[data-need-count]")?.getAttribute("data-need-count")).toBe("3");
    expect(screen.getByText(/Digest · ✓ 07:00/)).toBeTruthy();
    expect(screen.getByText("2 rooms active")).toBeTruthy();

  });

  it("says Nothing needs you, without internal change text (JTBD-1)", async () => {
    attention.workspaces.alpha = { needs: [], working: [], rooms_active: 0, live_call: null, last: "Sam changed Plan" };
    render(<StatusCard w={tile()} drag={drag} />);
    await screen.findByText("Nothing needs you");
  });

  it("a click aims the home composer and opens the workspace page on the right", async () => {
    render(<StatusCard w={tile()} drag={drag} />);
    const aim = heard(HOME_TARGET_EVENT);
    const nav = heard(VIEW_NAVIGATE_EVENT);
    fireEvent.click(document.querySelector("[data-status-card=alpha]")!);
    expect(aim.got).toEqual([{ workspace: "alpha" }]);
    expect(nav.got).toEqual([{ workspace: "alpha", path: ".workspace", label: "Alpha" }]);
    await waitFor(() => expect(document.querySelector("[data-status-card=alpha]")?.hasAttribute("data-target")).toBe(true));
    aim.off(); nav.off();
  });

  it("a read-only workspace is opened, never written to", () => {
    render(<StatusCard w={tile({ write: false, access: "reader", slug: "_global", name: "Company", workspace_kind: "company" })} drag={drag} />);
    const aim = heard(HOME_TARGET_EVENT);
    fireEvent.click(document.querySelector("[data-status-card=_global]")!);
    expect(aim.got).toEqual([]);
    aim.off();
  });

  it("an act that reaches beyond the screen asks first", async () => {
    attention.workspaces.alpha = { needs: [need()], working: [], rooms_active: 0, live_call: null, last: null };
    render(<StatusCard w={tile()} drag={drag} />);
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    expect(await screen.findByText("Accept this proposal?")).toBeTruthy();
    expect(calls.some((c) => c.url.includes("proposals"))).toBe(false);
    fireEvent.click(screen.getAllByRole("button", { name: "Accept" }).pop()!);
    await waitFor(() => expect(calls.find((c) => c.url === "/api/proposals/resolve")?.body).toEqual({ id: "p1", status: "ran" }));
  });

  it("opening a mention marks it read", async () => {
    attention.workspaces.alpha = { needs: [need({ id: "mention:m1", kind: "mention", text: "Mays asked you", act: { label: "Open", kind: "open-session", session: "s1", notification: "n9" } })], working: [], rooms_active: 0, live_call: null, last: null };
    render(<StatusCard w={tile()} drag={drag} />);
    fireEvent.click(await screen.findByRole("button", { name: "Open" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/api/notifications/read")?.body).toEqual({ ids: ["n9"] }));
  });
});

describe("the home with home2", () => {
  it("draws status cards in place of tiles, no Agents section, and the Now strip", async () => {
    attention = { generated_at: 1, needs: [need(), need({ id: "mention:x", workspace: "foreign", text: "Hidden thing" })], workspaces: { alpha: { needs: [need()], working: [], rooms_active: 0, live_call: null, last: null } } };
    render(<DesktopView desktop={desktop([tile()])} />);
    await screen.findAllByText("Move Search to Q2");
    expect(document.querySelector("[data-status-card=alpha]")).toBeTruthy();
    expect(document.querySelector("[data-workspace-tile]")).toBeNull();
    expect(document.querySelector("[data-desk-section=agents]")).toBeNull();
    // a need for a workspace not on this home is never named in the Now strip
    expect(document.querySelector("[data-now-strip]")?.textContent).not.toContain("Hidden thing");
  });

  it("cards are boxes in a grid: mark, name, ONE status line, at most two routine chips, members at the bottom (founder 2026-10-16)", async () => {
    attention = { generated_at: 1, needs: [], workspaces: { alpha: {
      needs: [need(), need({ id: "mention:y", text: "Second thing" })], rooms_active: 0, live_call: null, last: null,
      working: [1, 2, 3].map((i) => ({ kind: "routine", ref: `agent:r${i}`, label: `R${i}`, state: "ok", when: "daily", at: 1 })) as never } } };
    render(<DesktopView desktop={desktop([tile({ members: [{ subject: "u1", name: "Ann", initials: "A" }] } as never)])} />);
    await screen.findAllByText("Move Search to Q2");
    const card = document.querySelector("[data-status-card=alpha]") as HTMLElement;
    expect(card.closest("[data-home-grid]")).toBeTruthy();
    expect(card.querySelectorAll(".vx-scard-needs li")).toHaveLength(1);
    expect(card.querySelectorAll(".vx-scard-chip")).toHaveLength(2);
    const foot = card.querySelector("[data-card-foot]");
    expect(foot).toBeTruthy();
    expect(card.querySelector(".vx-scard-l1 .vx-scard-people")).toBeNull();
  });

  it("off, the home is the current one", () => {
    setFeatureOverride("all");
    render(<DesktopView desktop={desktop([tile()])} />);
    expect(document.querySelector("[data-status-card]")).toBeNull();
    expect(document.querySelector("[data-workspace-tile]")).toBeTruthy();
    expect(calls.some((c) => c.url === "/api/desktop/attention")).toBe(false);
  });
});

describe("NowStrip", () => {
  it("says a failed run in human words, like the workspace page (founder 2026-10-16)", async () => {
    attention = { generated_at: 1, needs: [need({ id: "failed:d", kind: "failed", text: "Nightly chat digest failed: no_outcome: the turn did not finish within the hour", act: { label: "Retry", kind: "retry-routine", ref: "agent:flow:digest" } })],
      workspaces: { alpha: { needs: [], working: [], rooms_active: 0, live_call: null, last: null } } };
    render(<NowStrip workspaces={[tile()]} />);
    expect(await screen.findByText("Nightly chat digest didn't finish.")).toBeTruthy();
    expect(document.body.textContent).not.toContain("no_outcome");
  });

  it("is absent when nothing is happening", async () => {
    render(<NowStrip workspaces={[tile()]} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    expect(document.querySelector("[data-now-strip]")).toBeNull();
  });
});

describe("the rich page", () => {
  it("never names a reference the reader may not see — not even the writer's label", async () => {
    refs = {
      "room:r1": { kind: "room", visible: true, name: "launch", members: 4, session: "r1" },
      "room:r2": { kind: "room", visible: false },
      "chat:c9": { kind: "chat", visible: false },
    };
    render(<RichPage slug="alpha" path="README.md" md={"Rooms: [[room:r1]] [[room:r2|Secret board]] [[chat:c9|Salary talk]]"} />);
    await screen.findByText("launch");
    expect(screen.getByText(/a private room/)).toBeTruthy();
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("Secret board");
    expect(text).not.toContain("Salary talk");
    expect(calls.find((c) => c.url === "/api/desktop/refs")?.body).toEqual({ refs: ["room:r1", "room:r2", "chat:c9"], workspace: "alpha" });
  });

  it("an action outside the allow-list is only its words; an allowed one asks first", async () => {
    refs = { "routine:flow:digest": { kind: "routine", visible: true, name: "Weekly digest" } };
    render(<RichPage slug="alpha" path="p.md" md={"[[action:delete-workspace:alpha | Delete it]] [[action:run-routine:flow:digest | Run digest]]"} />);
    expect(screen.queryByRole("button", { name: /Delete it/ })).toBeNull();
    expect(screen.getByText("Delete it")).toBeTruthy();
    const ask = heard(ASK_CHAT_EVENT);
    fireEvent.click(screen.getByRole("button", { name: /Run digest/ }));
    expect(await screen.findByText("Run “Weekly digest”?")).toBeTruthy();
    expect(ask.got).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Run it now" }));
    await waitFor(() => expect(ask.got).toHaveLength(1));
    ask.off();
  });

  it("raw HTML is inert", () => {
    render(<RichPage slug="alpha" path="p.md" md={'[[room:r1]]\n\n<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>'} />);
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("[onerror]")).toBeNull();
  });

  it("columns and a callout are laid out", () => {
    render(<RichPage slug="alpha" path="p.md" md={":::columns\nLeft side\n---\nRight side\n:::\n:::callout\nHeads up\n:::"} />);
    expect(document.querySelectorAll(".vx-rp-col")).toHaveLength(2);
    expect(document.querySelector(".vx-rp-callout")).toBeTruthy();
  });
});

describe("the workspace page", () => {
  it("is a status block (needs you, agents) over the README; no chats, calls, rooms or file lists", async () => {
    one = { needs: [need()], working: [], rooms_active: 1, live_call: null, last: null,
      rooms: [{ session: "r1", name: "launch", members: 3, last_active: 1 }],
      chats: [{ session: "c1", name: "A chat", last_active: 1 }],
      routines: [{ ref: "agent:flow:brief", name: "Brief", state: "ok", when: "daily", last_at: 100, next_at: null, session: "flow-brief",
        runs: [{ at: 300, ok: true, what: "Ran" }, { at: 200, ok: false, what: "Nightly chat digest failed: no_outcome: the turn did not finish within the hour" }] }],
      calls: [{ meeting: "m1", name: "Call with Vexa", state: "ended", at: 1 }] };
    files = { "README.md": "# Alpha\n\nWhat this is for.", "plan.md": "# Plan" };
    render(<WorkspacePage slug="alpha" />);
    await screen.findByText("Brief");
    await screen.findByText("What this is for.");
    const status = document.querySelector("[data-wsp-status]") as HTMLElement;
    expect([...status.querySelectorAll("[data-sec]")].map((x) => x.getAttribute("data-sec"))).toEqual(["needs", "routines"]);
    expect([...document.querySelectorAll("[data-sec]")].map((x) => x.getAttribute("data-sec"))).toEqual(["needs", "routines"]);
    expect(document.querySelector("[data-wsp-readme]")?.textContent).toContain("What this is for.");
    expect(screen.queryByText("A chat")).toBeNull();
    expect(screen.queryByText("Call with Vexa")).toBeNull();
    expect(screen.queryByText(/Last \d+ runs/)).toBeNull();
    expect(document.body.textContent).not.toContain("no_outcome");
    const open = heard(OPEN_SESSION_EVENT);
    fireEvent.click(screen.getByRole("button", { name: /Open Brief/ }));
    expect(open.got).toEqual([{ session: "flow-brief" }]);
    open.off();
  });

  it("New room makes a room in this workspace, and New chat starts a chat aimed there", async () => {
    files = { "README.md": "# Alpha" };
    render(<WorkspacePage slug="alpha" />);
    const nr = heard(NEW_ROOM_EVENT), nc = heard(NEW_CHAT_AT_EVENT);
    fireEvent.click(await screen.findByRole("button", { name: /New room/ }));
    expect(nr.got).toEqual([{ workspace: "alpha" }]);
    fireEvent.click(screen.getByRole("button", { name: /New chat/ }));
    expect(nc.got).toEqual([{ workspace: "alpha" }]);
  });

  it("says a failed run in human words", () => {
    expect(humanStatus("Nightly chat digest failed: no_outcome: the turn did not finish within the hour")).toBe("Nightly chat digest didn't finish.");
    expect(humanStatus("Weekly brief failed: tool_error: gmail refused")).toBe("Weekly brief failed: gmail refused");
  });

});

import { LiveTable } from "../desktop/home2/LiveTable";
describe("a plain table", () => {
  const table = <LiveTable><thead><tr><th>Name</th><th>Status</th></tr></thead>
    <tbody><tr><td>Beta</td><td>open</td></tr><tr><td>Alpha</td><td>done</td></tr><tr><td>Gamma</td><td>open</td></tr></tbody></LiveTable>;
  const names = () => [...document.querySelectorAll("tbody tr td:first-child")].map((td) => td.textContent);
  it("searches, filters a column by value and sorts by a header", () => {
    render(table);
    fireEvent.change(screen.getByLabelText("Search this table"), { target: { value: "gam" } });
    expect(names()).toEqual(["Gamma"]);
    fireEvent.change(screen.getByLabelText("Search this table"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Filter by Status"), { target: { value: "open" } });
    expect(names()).toEqual(["Beta", "Gamma"]);
    fireEvent.change(screen.getByLabelText("Filter by Status"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Name" }));
    expect(names()).toEqual(["Alpha", "Beta", "Gamma"]);
    fireEvent.click(screen.getByRole("button", { name: /Name/ }));
    expect(names()).toEqual(["Gamma", "Beta", "Alpha"]);
  });
});

/** YOUR OWN VIEW OF EACH WORKSPACE (#1783, R3 3.4; founder-approved: per-person state lives on the CRM
 *  `member_of` relation, crm-api the sole writer — never browser storage). Pinned workspaces lead the
 *  home; one hidden from home is listed with Show again; the workspace page's folded sections are the
 *  person's setting; a refused write is rolled back; nothing is written to browser storage. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../../ui-kit/primitives/Toast", () => ({ toast: () => undefined, Toaster: () => null }));

import { setFeatureOverride } from "../../platform/features";
import type { WorkspaceItem } from "../../platform/desktop";
import { HomeCards } from "../desktop/home2/StatusCard";
import { WorkspacePage } from "../desktop/home2/WorkspacePage";
import { resetAttention } from "../desktop/home2/attentionApi";
import { resetSettings, setSettings } from "../desktop/home2/settingsApi";
import { resetDesktopStore } from "../desktop/desktopApi";

const drag = () => ({ onDragStart: () => {}, onDragOver: () => {}, onDrop: () => {} });
const tile = (slug: string, name: string): WorkspaceItem => ({
  kind: "workspace", ref: `workspace:${slug}`, workspace_kind: "shared", slug, id: slug, name, access: "owner", write: true,
  members: [], last_activity: null, last_call: null, live_call: null,
});

let stored: Record<string, Record<string, unknown>>;
let refuse: boolean;
let posts: unknown[];

beforeEach(() => {
  stored = {}; refuse = false; posts = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const u = String(url);
    const ok = (x: unknown) => Promise.resolve(new Response(JSON.stringify(x), { status: 200 }));
    if (u === "/api/desktop/workspace-settings" && init?.method === "POST") {
      const b = JSON.parse(String(init.body));
      posts.push(b);
      if (refuse) return Promise.resolve(new Response("{}", { status: 403 }));
      stored[b.workspace] = { ...(stored[b.workspace] ?? {}), ...b.settings };
      return ok({ workspace: b.workspace, settings: stored[b.workspace] });
    }
    if (u === "/api/desktop/workspace-settings") return ok({ workspaces: stored });
    if (u === "/api/desktop/attention") return ok({ generated_at: 1, needs: [], workspaces: {} });
    if (u.startsWith("/api/workspace/tree")) return ok({ files: [] });
    return ok({});
  }));
  setFeatureOverride({ home2: true });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); resetSettings(); resetAttention(); resetDesktopStore(); setFeatureOverride("all"); });

const order = () => [...document.querySelectorAll("[data-status-card]")].map((e) => e.getAttribute("data-status-card"));

describe("the home in your own order", () => {
  it("pinned first; hidden on one line with Show again, which writes the setting", async () => {
    stored = { beta: { pinned: true }, gamma: { hidden: true } };
    render(<HomeCards items={[tile("alpha", "Alpha"), tile("beta", "Beta"), tile("gamma", "Gamma")]} tileDrag={drag} />);
    await waitFor(() => expect(order()).toEqual(["beta", "alpha"]));
    expect(screen.getByText(/Hidden from home/).textContent).toContain("Gamma");
    fireEvent.click(screen.getByRole("button", { name: "Show again" }));
    await waitFor(() => expect(order()).toEqual(["beta", "alpha", "gamma"]));
    expect(posts).toEqual([{ workspace: "gamma", settings: { hidden: false } }]);
  });

  it("a refused write is rolled back, and nothing goes to browser storage", async () => {
    const spy = vi.spyOn(Storage.prototype, "setItem");
    render(<HomeCards items={[tile("alpha", "Alpha"), tile("beta", "Beta")]} tileDrag={drag} />);
    await waitFor(() => expect(order()).toEqual(["alpha", "beta"]));
    refuse = true;
    await setSettings("beta", { pinned: true });
    await waitFor(() => expect(order()).toEqual(["alpha", "beta"]));
    expect(spy.mock.calls.filter(([k]) => /settings|wsp|pin|hidden/i.test(String(k)))).toEqual([]);
  });
});

describe("the workspace page's folded sections", () => {
  it("are the person's setting: a fold writes `collapsed`, and a folded one reads closed", async () => {
    stored = { alpha: { collapsed: ["needs"] } };
    vi.mocked(fetch).mockImplementation(((url: string, init?: RequestInit) => {
      const u = String(url);
      const ok = (x: unknown) => Promise.resolve(new Response(JSON.stringify(x), { status: 200 }));
      if (u === "/api/desktop/workspace-settings" && init?.method === "POST") {
        const b = JSON.parse(String(init.body)); posts.push(b);
        stored[b.workspace] = { ...(stored[b.workspace] ?? {}), ...b.settings };
        return ok({ workspace: b.workspace, settings: stored[b.workspace] });
      }
      if (u === "/api/desktop/workspace-settings") return ok({ workspaces: stored });
      if (u === "/api/desktop/attention") return ok({ generated_at: 1, needs: [], workspaces: { alpha: { needs: [], working: [], rooms_active: 1, live_call: null, last: null,
        rooms: [], chats: [], calls: [], routines: [{ ref: "agent:flow:brief", name: "launch", state: "ok", when: "daily", last_at: 1, next_at: null, runs: [] }],
        needs: [{ id: "n1", kind: "mention", text: "hi", act: { kind: "open", label: "Open" } }] } } });
      if (u.startsWith("/api/workspace/tree")) return ok({ files: ["README.md"] });
      return ok({});
    }) as typeof fetch);
    render(<WorkspacePage slug="alpha" />);
    await screen.findByText("launch");
    await waitFor(() => expect(document.querySelector("[data-sec=needs]")?.hasAttribute("data-open")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: /Agents and routines/ }));
    await waitFor(() => expect(posts).toEqual([{ workspace: "alpha", settings: { collapsed: ["needs", "routines"] } }]));
    expect(screen.queryByText("launch")).toBeNull();
  });
});

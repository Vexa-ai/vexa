/** MAKING A WORKSPACE (#1783, R3 3.5, `home2`; mock flow 12). Pinned: the doors it uses (private →
 *  `/api/workspace/new`, shared → `/api/workspace/shared/new`), a seeded rich README, the identity
 *  PROPOSED (a site's logo with `propose: true`, else the agent asked to design one), Open offered
 *  only while `open_workspaces` is on, invitations DRAFTED in the person's chat (never sent), and the
 *  proposal answered on the card (Use / Decline) through the owner's identity route. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../../ui-kit/primitives/Toast", () => ({ toast: () => undefined, Toaster: () => null }));

import { setFeatureOverride } from "../../platform/features";
import { ASK_CHAT_EVENT, HOME_TARGET_EVENT } from "../../platform/events";
import type { WorkspaceItem } from "../../platform/desktop";
import { NewWorkspaceDialog, emailsOf, seedReadme, siteOf } from "../desktop/home2/NewWorkspace";
import { StatusCard } from "../desktop/home2/StatusCard";
import { resetAttention } from "../desktop/home2/attentionApi";
import { resetDesktopStore } from "../desktop/desktopApi";
import { resetSettings } from "../desktop/home2/settingsApi";

type Call = { url: string; method: string; body: unknown };
let calls: Call[];
let attention: unknown;

function heard(name: string) {
  const got: unknown[] = [];
  const on = (e: Event) => got.push((e as CustomEvent).detail);
  window.addEventListener(name, on);
  return { got, off: () => window.removeEventListener(name, on) };
}

beforeEach(() => {
  calls = [];
  attention = { generated_at: 1, needs: [], workspaces: {} };
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const u = String(url);
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url: u, method: init?.method ?? "GET", body });
    const ok = (x: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(x), { status }));
    if (u === "/api/workspace/shared/new") return ok({ workspace_id: "kestrel-1a2b", role: "owner", name: body.name }, 201);
    if (u === "/api/workspace/new") return ok({ slug: "notes", created: "notes", name: body.name }, 201);
    if (u === "/api/workspace/members/add") return ok({ detail: "no account here" }, 404);
    if (u === "/api/workspace/invite-link") return ok({ url: "https://app.example.test/join/tok", workspace_id: body.slug }, 201);
    if (u.includes("/identity/icon") && init?.method === "PUT") return ok({ proposed: true }, body?.site === "nologo.example" ? 404 : 202);
    if (u.includes("/identity/proposal")) return ok({ set: !!body.accept });
    if (u === "/api/desktop/attention") return ok(attention);
    return ok({});
  }));
  setFeatureOverride({ home2: true, open_workspaces: false });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); resetAttention(); resetDesktopStore(); resetSettings(); setFeatureOverride("all"); });

const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

describe("the pure parts", () => {
  it("reads a site as its domain, addresses as a de-duplicated list, and seeds a rich README", () => {
    expect(siteOf("https://www.Kestrel.example/about")).toBe("www.kestrel.example");
    expect(siteOf("not a site")).toBeNull();
    expect(emailsOf("ruth@k.example, li@k.example;ruth@k.example bad")).toEqual(["ruth@k.example", "li@k.example"]);
    const md = seedReadme("Kestrel pilot", "The insurance pilot.");
    expect(md.startsWith("# Kestrel pilot\n\nThe insurance pilot.")).toBe(true);
    expect(md).toContain("{{status}}");
  });
});

describe("New workspace", () => {
  it("shared: made through its door, README seeded, the site's logo PROPOSED, aimed at, then invites are drafted", async () => {
    const aim = heard(HOME_TARGET_EVENT);
    const ask = heard(ASK_CHAT_EVENT);
    render(<NewWorkspaceDialog open onClose={() => {}} />);
    expect(screen.queryByText("Open")).toBeNull();                 // open_workspaces is off
    type("Name", "Kestrel pilot");
    type("What is it for", "The insurance pilot.");
    type("Site", "kestrel.example");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText("Invite to Kestrel pilot");
    expect(calls.find((c) => c.url === "/api/workspace/shared/new")?.body).toEqual({ name: "Kestrel pilot" });
    const readme = calls.find((c) => c.url.startsWith("/api/workspace/file") && c.method === "PUT");
    expect(readme?.body).toMatchObject({ path: "README.md", slug: "kestrel-1a2b" });
    expect(String((readme?.body as { content: string }).content)).toContain("The insurance pilot.");
    const icons = calls.filter((c) => c.url.includes("/identity/icon") && c.method === "PUT");
    expect(icons.map((c) => c.url)).toEqual(["/api/workspaces/kestrel-1a2b/identity/icon", "/api/workspaces/kestrel-1a2b/identity/icon"]);
    expect(icons[0].body).toEqual({ glyph: "auto" });               // its standard icon, picked at once
    expect(icons[1].body).toEqual({ site: "kestrel.example", propose: true });
    expect(aim.got).toEqual([{ workspace: "kestrel-1a2b" }]);
    expect(ask.got).toEqual([]);                                     // a found logo needs no agent turn
    type("People to invite", "ruth@kestrel.example, li@kestrel.example");
    fireEvent.click(screen.getByRole("button", { name: "Invite 2" }));
    await waitFor(() => expect(ask.got).toHaveLength(1));
    expect(calls.find((c) => c.url === "/api/workspace/invite-link")?.body).toMatchObject({ slug: "kestrel-1a2b", max_uses: 2 });
    const d = ask.got[0] as { prompt: string; mode?: string };
    expect(d.mode).toBeUndefined();                                   // a real turn: the agent's draft shows in the chat
    expect(calls.filter((c) => c.url === "/api/workspace/members/add")).toHaveLength(2);  // tried in-app first
    expect(d.prompt).toContain("do not send");
    expect(d.prompt).toContain("https://app.example.test/join/tok");
    expect(calls.some((c) => /gmail|send/i.test(c.url))).toBe(false);
    aim.off(); ask.off();
  });

  it("private, no site: a standard icon is picked from the name and purpose, no agent turn; no invite step", async () => {
    const ask = heard(ASK_CHAT_EVENT);
    const closed = vi.fn();
    render(<NewWorkspaceDialog open onClose={closed} />);
    type("Name", "My notes");
    fireEvent.click(screen.getByRole("radio", { name: /Private/ }));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(closed).toHaveBeenCalled());
    expect(calls.find((c) => c.url === "/api/workspace/new")?.body).toEqual({ name: "My notes" });
    expect(calls.some((c) => c.url === "/api/workspace/shared/new")).toBe(false);
    const icons = calls.filter((c) => c.url.includes("/identity/icon") && c.method === "PUT");
    expect(icons.map((c) => c.body)).toEqual([{ glyph: "auto" }]);
    expect(ask.got).toEqual([]);
    ask.off();
  });

  it("Open is shown only while open_workspaces is on, and stays unselectable until a door makes one", () => {
    setFeatureOverride({ home2: true, open_workspaces: true });
    render(<NewWorkspaceDialog open onClose={() => {}} />);
    expect((screen.getByRole("radio", { name: /Open/ }) as HTMLInputElement).disabled).toBe(true);
  });
});

describe("the proposed identity on the new card", () => {
  const tile: WorkspaceItem = { kind: "workspace", ref: "workspace:kestrel-1a2b", workspace_kind: "shared", slug: "kestrel-1a2b", id: "kestrel-1a2b",
    name: "Kestrel pilot", access: "owner", write: true, members: [], last_activity: null, last_call: null, live_call: null };
  const need = { id: "proposal:identity-kestrel-1a2b", kind: "proposal", workspace: "kestrel-1a2b", text: "Vexa proposes its logo for Kestrel pilot", at: 1,
    act: { label: "Use", kind: "accept-proposal", source: "identity", proposal: "identity-kestrel-1a2b", slug: "kestrel-1a2b" } };

  it("shows the proposal itself, and Use answers through the owner's identity route", async () => {
    attention = { generated_at: 1, needs: [need], workspaces: { "kestrel-1a2b": { needs: [need], working: [], rooms_active: 0, live_call: null, last: null } } };
    render(<StatusCard w={tile} drag={{ onDragStart: () => {}, onDragOver: () => {}, onDrop: () => {} }} />);
    await screen.findByText("Vexa proposes its logo for Kestrel pilot");
    expect(document.querySelector(".vx-scard-prop")?.getAttribute("src")).toBe("/api/workspaces/kestrel-1a2b/identity/icon?proposal=1");
    fireEvent.click(screen.getByRole("button", { name: "Use" }));
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(calls.find((c) => c.url.includes("/identity/proposal"))?.body).toEqual({ accept: true }));
  });

  it("Decline drops it", async () => {
    attention = { generated_at: 1, needs: [need], workspaces: { "kestrel-1a2b": { needs: [need], working: [], rooms_active: 0, live_call: null, last: null } } };
    render(<StatusCard w={tile} drag={{ onDragStart: () => {}, onDragOver: () => {}, onDrop: () => {} }} />);
    fireEvent.click(await screen.findByRole("button", { name: "Decline" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/api/workspaces/kestrel-1a2b/identity/proposal")?.body).toEqual({ accept: false }));
  });
});


import { DesktopView as _DV } from "../desktop/DesktopView";
describe("+ New workspace is a conversation (founder 2026-10-16, UI 7g)", () => {
  it("asks the agent to set it up with inline cards, and opens no form", async () => {
    const { HomeCards } = await import("../desktop/home2/StatusCard");
    const ask = heard(ASK_CHAT_EVENT);
    render(<HomeCards items={[]} tileDrag={() => ({ onDragStart: () => {}, onDragOver: () => {}, onDrop: () => {} })} />);
    fireEvent.click(screen.getByRole("button", { name: "+ New workspace" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    const d = ask.got[0] as { prompt: string; display: string };
    expect(d.display).toBe("Set up a new workspace");
    expect(d.prompt).toContain("```ask");
    expect(d.prompt).toContain("workspace_new");
    ask.off();
    void _DV;
  });
});

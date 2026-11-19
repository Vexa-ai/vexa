/** THE PROFILE CHIP (Vexa-ai/vexa#1783, UI decision 6, founder 2026-10-14: "profile creation from
 *  chat, but profile choice is better here"). The chip names the profile first; its menu lists the
 *  person's profiles with a one-line summary and a check on the active one; picking switches the chat;
 *  Create / Edit / Save START A CONVERSATION (a short visible line, a hidden instruction) — they never
 *  just fill the composer. Server half: `core/agent/tests/test_chat_profiles.py`. */
import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, cleanup, act, fireEvent, screen } from "@testing-library/react";

import { ScopeLine, scopeDetails, INCOGNITO_NOTE, ASKS } from "../ScopeLine";
import { notifyScope, isScopeTool } from "../../surfaces/scopeEvents";
import { ASK_CHAT_EVENT } from "../../platform/events";
import type { ScopeView } from "../../data/workspaceApi";

const view = (over: Partial<ScopeView> = {}): ScopeView => ({
  workspaces: [
    { slug: "personal", name: "Personal", kind: "personal", role: "owner", state: "active", reads: true, target: false },
    { slug: "biz", name: "biz", display: "biz (yours · biz · empty)", kind: "private", role: "owner", state: "active", reads: false, target: false },
    { slug: "biz-864aed", name: "biz", display: "biz (shared · biz-864aed)", kind: "shared", role: "owner", state: "active", reads: true, target: true },
  ],
  hidden: 2,
  line: "Work · writes to biz (shared · biz-864aed)",
  chat: { session: "c1", target: "biz-864aed", reads: ["personal", "biz-864aed"], own_reads: true, profile: "work", changed: false },
  defaults: { target: "biz-864aed", active: ["personal", "biz-864aed"] },
  ...over,
});
const PROFILES = {
  profiles: [
    { name: "work", target: "biz-864aed", reads: null, incognito: false, summary: "writes to biz (shared · biz-864aed) · reads your active workspaces · memory on" },
    { name: "study", target: null, reads: null, incognito: false, summary: "writes to Personal · reads your active workspaces · memory on" },
    { name: "incognito", target: null, reads: [], incognito: true, summary: "no workspaces · nothing remembered" },
  ],
  default: "study", active: "work",
};

let calls: { url: string; body?: string }[] = [];
let answer: ScopeView = view();
beforeEach(() => {
  calls = []; answer = view();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), body: init?.body as string | undefined });
    const body = String(url).startsWith("/api/workspace/profiles") && !init?.method ? PROFILES
      : String(url).startsWith("/api/workspace/states") ? answer : { say: "ok" };
    return { ok: true, status: 200, json: async () => body } as unknown as Response;
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const settle = () => act(async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); });
const open = () => fireEvent.click(document.querySelector("[data-scope-trigger]")!);

describe("the profile chip", () => {
  it("names the profile first, from the server", async () => {
    render(<ScopeLine session="c1" />);
    await settle();
    expect(document.querySelector("[data-scope-text]")?.textContent).toBe("Work · writes to biz (shared · biz-864aed)");
  });

  it("lists the profiles with a summary each and a check on the active one", async () => {
    render(<ScopeLine session="c1" />);
    await settle();
    open();
    const work = document.querySelector('[data-profile="work"]')!;
    expect(work.getAttribute("aria-checked")).toBe("true");
    expect(document.querySelector('[data-profile="incognito"]')?.textContent).toContain("no workspaces · nothing remembered");
    expect(document.querySelector('[data-profile="study"]')?.textContent).toContain("default");
  });

  it("opens on the detail the chip leaves out: where it writes, what it reads by name, memory", async () => {
    render(<ScopeLine session="c1" />);
    await settle();
    open();
    const t = (k: string) => document.querySelector(`[data-scope-detail="${k}"]`)?.textContent;
    expect(t("writes")).toBe("Saves to biz (shared · biz-864aed)");
    expect(t("reads")).toBe("Reads Personal, biz (shared · biz-864aed)");
    expect(t("memory")).toBe("Memory on");
    // then Incognito, then the person's own profiles, then Create — and nothing else
    const order = [...document.querySelectorAll("[data-profile], [data-ctx]")].map((e) =>
      e.getAttribute("data-profile") ?? e.getAttribute("data-ctx"));
    expect(order).toEqual(["incognito", "work", "study", "profile-create"]);
  });

  it("picking a profile switches this chat on the server", async () => {
    render(<ScopeLine session="c1" />);
    await settle();
    open();
    fireEvent.click(document.querySelector('[data-profile="incognito"]')!);
    await settle();
    const post = calls.find((c) => c.url === "/api/workspace/profile");
    expect(JSON.parse(String(post?.body))).toEqual({ name: "incognito", session: "c1" });
  });

  it("Create profile starts a conversation: a short visible line, the instruction hidden", async () => {
    const seen: CustomEvent[] = [];
    const on = (e: Event) => seen.push(e as CustomEvent);
    window.addEventListener(ASK_CHAT_EVENT, on);
    render(<ScopeLine session="c1" />);
    await settle();
    open();
    fireEvent.click(document.querySelector('[data-ctx="profile-create"]')!);
    window.removeEventListener(ASK_CHAT_EVENT, on);
    expect(seen[0].detail).toEqual({ hidden: false, display: "Create a chat profile", session: "c1", prompt: ASKS.create.prompt });
    expect(ASKS.create.prompt).toContain("chat_profile_edit");
  });

  it("says honestly what incognito keeps", () => {
    expect(scopeDetails(view({ chat: { session: "c1", target: null, reads: [], own_reads: true, profile: "incognito" } }))).toBe(INCOGNITO_NOTE);
  });

  it("re-reads when the agent changes the scope", async () => {
    render(<ScopeLine session="c1" />);
    await settle();
    answer = view({ line: "Incognito", chat: { session: "c1", target: null, reads: [], own_reads: true, profile: "incognito" } });
    await act(async () => { notifyScope(); });
    await settle();
    expect(document.querySelector("[data-scope-text]")?.textContent).toBe("Incognito");
  });

  it("is moved by the state, target, profile and defaults verbs", () => {
    for (const t of ["mcp__vexa__workspace_state_set", "mcp__vexa__chat_profile_set", "mcp__vexa__chat_profile_edit"]) expect(isScopeTool(t)).toBe(true);
    expect(isScopeTool("mcp__vexa__workspace_read")).toBe(false);
  });

  it("a profiles answer without a list draws the chip and an empty menu, never a crash", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () =>
      (String(url).startsWith("/api/workspace/states") ? view() : {}) }) as unknown as Response));
    render(<ScopeLine session="c1" />);
    await settle();
    open();
    expect(document.querySelector("[data-scope-text]")?.textContent).toMatch(/^Work/);
    expect(document.querySelectorAll("[data-profile]").length).toBe(0);
  });

  it("shows nothing for the admin account", async () => {
    answer = view({ admin_account: true });
    render(<ScopeLine session="c1" />);
    await settle();
    expect(document.querySelector("[data-scope-line]")).toBeNull();
  });
});

import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrowserSessionView } from "../BrowserSessionView";
import { BOT_ACCOUNTS_CHANGED, BROWSER_SESSION_OPEN, browserSessionFromToolResult, startBrowserSession, viewSrc } from "../browserSession";
import { toolResultUiEvent } from "../../surfaces/toolUiEvents";

const ID = "0123456789abcdef0123456789abcdef";
const PW = "pw-Secret1";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

function sessionFetch(statuses: string[]) {
  const calls: Array<[string, RequestInit | undefined]> = [];
  let i = 0;
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push([url, init]);
    if (init?.method === "DELETE") return new Response(JSON.stringify({ session_id: ID, status: "closed" }), { status: 200 });
    const status = statuses[Math.min(i++, statuses.length - 1)];
    return new Response(JSON.stringify({ session_id: ID, status, platform: "google", owner: "user:1", account_id: null, detail: status === "failed" ? "Timed out." : null }), { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

const open = () => act(async () => {
  window.dispatchEvent(new CustomEvent(BROWSER_SESSION_OPEN, { detail: { session_id: ID, vnc_password: PW, platform: "google" } }));
});

describe("the live view URL", () => {
  it("carries the password in the fragment only", () => {
    const src = viewSrc(ID, PW);
    const [beforeHash, fragment] = src.split("#");
    expect(beforeHash).toBe(`/api/bot-accounts/sessions/${ID}/view/vnc.html`);
    expect(beforeHash).not.toContain(PW);
    const params = new URLSearchParams(fragment);
    expect(params.get("autoconnect")).toBe("1");
    expect(params.get("resize")).toBe("remote");
    expect(params.get("path")).toBe(`bot-accounts/sessions/${ID}/vnc`);
    expect(params.get("password")).toBe(PW);
  });
});

describe("BrowserSessionView", () => {
  it("renders nothing until a session opens, and ignores malformed events", async () => {
    sessionFetch(["starting"]);
    render(<BrowserSessionView />);
    expect(screen.queryByRole("dialog")).toBeNull();
    await act(async () => { window.dispatchEvent(new CustomEvent(BROWSER_SESSION_OPEN, { detail: { session_id: "nope", vnc_password: PW } })); });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("polls the session, shows the live browser when ready, and reports the saved account", async () => {
    vi.useFakeTimers();
    const { calls } = sessionFetch(["starting", "ready", "saved"]);
    const changed = vi.fn();
    window.addEventListener(BOT_ACCOUNTS_CHANGED, changed);
    render(<BrowserSessionView pollMs={2000} />);
    await open();
    expect(screen.getByRole("dialog", { name: "Sign in a bot account" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("Starting");
    expect(document.querySelector("iframe")).toBeNull();

    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    const frame = document.querySelector("iframe") as HTMLIFrameElement;
    expect(frame.getAttribute("src")).toBe(viewSrc(ID, PW));

    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByRole("status").textContent).toBe("Signed in — saved as a bot account");
    expect(document.querySelector("iframe")).toBeNull();
    expect(changed).toHaveBeenCalled();
    // polling stops once the session is final, and no request carried the password
    const n = calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(calls.length).toBe(n);
    expect(calls.every(([url]) => url === `/api/bot-accounts/sessions/${ID}` && !url.includes(PW))).toBe(true);
    window.removeEventListener(BOT_ACCOUNTS_CHANGED, changed);
  });

  it("Close deletes a running session and hides the view", async () => {
    const { calls } = sessionFetch(["ready"]);
    render(<BrowserSessionView />);
    await open();
    await screen.findByTitle("Google sign-in browser");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.some(([url, init]) => url === `/api/bot-accounts/sessions/${ID}` && init?.method === "DELETE")).toBe(true);
  });

  it("shows why a sign-in failed", async () => {
    sessionFetch(["failed"]);
    render(<BrowserSessionView />);
    await open();
    await vi.waitFor(() => expect(screen.getByRole("status").textContent).toContain("Timed out."));
    expect(screen.getByRole("button", { name: "Done" })).toBeTruthy();
  });
});

describe("opening from the agent and from the panel", () => {
  it("reads a browser_session_open result that asks to open the view", () => {
    const body = { ui_action: "open_browser_session", session_id: ID, vnc_password: PW, platform: "zoom", status: "starting" };
    expect(browserSessionFromToolResult("mcp__vexa__browser_session_open", body)).toEqual({ session_id: ID, vnc_password: PW, platform: "zoom" });
    expect(browserSessionFromToolResult("browser_session_open", JSON.stringify(body))?.session_id).toBe(ID);
    expect(browserSessionFromToolResult("mcp__vexa__bot_accounts_list", body)).toBeNull();
    expect(browserSessionFromToolResult("mcp__vexa__browser_session_open", { ...body, ui_action: undefined })).toBeNull();
    expect(browserSessionFromToolResult("mcp__vexa__browser_session_open", "not json")).toBeNull();
  });

  it("the chat opens the view from a successful tool result", () => {
    const src = readFileSync(resolve(__dirname, "../../surfaces/chat.tsx"), "utf8");
    const hook = src.slice(src.indexOf("onToolResult:"), src.indexOf("onTool: (tool, args)"));
    // surfaces sit below this shell, so the chat raises the event through surfaces/toolUiEvents.ts
    expect(hook).toContain("r.ok ? toolResultUiEvent(tool, r.result)");
    expect(hook).toContain("new CustomEvent(ui.name, { detail: ui.detail })");
    expect(src).not.toMatch(/from "\.\.\/minutes\//);
    // the event name surfaces restate is this shell's
    const body = { ui_action: "open_browser_session", session_id: ID, vnc_password: PW, platform: "zoom" };
    expect(toolResultUiEvent("mcp__vexa__browser_session_open", body)?.name).toBe(BROWSER_SESSION_OPEN);
  });

  it("startBrowserSession POSTs the session and opens the view", async () => {
    const fn = vi.fn(async () => new Response(JSON.stringify({ session_id: ID, vnc_password: PW, platform: "microsoft", status: "starting" }), { status: 201 }));
    vi.stubGlobal("fetch", fn);
    const seen = vi.fn();
    window.addEventListener(BROWSER_SESSION_OPEN, seen);
    await startBrowserSession("microsoft", "company", "f".repeat(32));
    window.removeEventListener(BROWSER_SESSION_OPEN, seen);
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/bot-accounts/sessions");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ platform: "microsoft", owner: "company", account_id: "f".repeat(32) });
    expect((seen.mock.calls[0][0] as CustomEvent).detail).toEqual({ session_id: ID, vnc_password: PW, platform: "microsoft" });
  });

  it("startBrowserSession surfaces a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Bot accounts are not available." }), { status: 403 })));
    await expect(startBrowserSession("google")).rejects.toThrow("Bot accounts are not available.");
  });
});

describe("an agent-opened view (ADR-0049)", () => {
  it("opens from the harness's browser-session path and reads its password from meeting-api", async () => {
    const { browserSessionFromOpenPath } = await import("../browserSession");
    expect(browserSessionFromOpenPath("browser-session:nope")).toBeNull();
    const detail = browserSessionFromOpenPath(`browser-session:${ID}`);
    expect(detail).toEqual({ session_id: ID, vnc_password: "", platform: "" });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ session_id: ID, status: "ready", platform: "google", vnc_password: PW }), { status: 200 })));
    render(<BrowserSessionView pollMs={10} />);
    await act(async () => { window.dispatchEvent(new CustomEvent(BROWSER_SESSION_OPEN, { detail })); });
    const frame = await screen.findByTitle(/sign-in browser/);
    expect(frame.getAttribute("src")).toBe(viewSrc(ID, PW));
  });
});

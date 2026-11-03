import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BotAccounts } from "../BotAccounts";
import { ConnectionsPanel, CONNECTIONS_OPEN } from "../ConnectionsPanel";
import { BROWSER_SESSION_OPEN } from "../browserSession";

const MINE = "a".repeat(32), COMPANY = "b".repeat(32), SESSION = "c".repeat(32);
const ACCOUNTS = [
  { id: MINE, owner: "user:7", platform: "google", display_name: "Work Google", state: "signed_out", usable: true, mine: true },
  { id: COMPANY, owner: "company", platform: "zoom", display_name: "Company Zoom", state: "ok", usable: true, mine: false },
];

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function backend({ admin = false } = {}) {
  const calls: Array<[string, RequestInit | undefined]> = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push([url, init]);
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
    if (url === "/api/auth/me") return json({ authenticated: true, admin_account: admin });
    if (url === "/api/bot-accounts") return json({ accounts: ACCOUNTS, company_bot_use: "all" });
    if (url === "/api/bot-accounts/sessions") return json({ session_id: SESSION, vnc_password: "pw", platform: JSON.parse(String(init?.body)).platform, status: "starting" }, 201);
    if (url.startsWith("/api/bot-accounts/") && init?.method === "DELETE") return json({ account_id: url.split("/").pop(), deleted: true });
    return json({ connections: [] });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}
const postBody = (calls: Array<[string, RequestInit | undefined]>) =>
  JSON.parse(String(calls.find(([u, i]) => u === "/api/bot-accounts/sessions" && i?.method === "POST")![1]!.body));

describe("Bot accounts", () => {
  it("lists accounts with platform, owner and state", async () => {
    backend();
    render(<BotAccounts />);
    const mine = (await screen.findByText("Work Google")).closest("[data-bot-account]") as HTMLElement;
    expect(within(mine).getByText("Google · Me")).toBeTruthy();
    expect(within(mine).getByText("Signed out")).toBeTruthy();
    const company = screen.getByText("Company Zoom").closest("[data-bot-account]") as HTMLElement;
    expect(within(company).getByText("Zoom · Company")).toBeTruthy();
    expect(within(company).getByText("OK")).toBeTruthy();
  });

  it("shows a company account read-only to everyone but the admin account", async () => {
    backend();
    render(<BotAccounts />);
    const company = (await screen.findByText("Company Zoom")).closest("[data-bot-account]") as HTMLElement;
    expect(within(company).queryByRole("button")).toBeNull();
  });

  it("signs a signed-out account in again", async () => {
    const calls = backend();
    const opened = vi.fn();
    window.addEventListener(BROWSER_SESSION_OPEN, opened);
    render(<BotAccounts />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign in again" }));
    await waitFor(() => expect(opened).toHaveBeenCalled());
    window.removeEventListener(BROWSER_SESSION_OPEN, opened);
    expect(postBody(calls)).toEqual({ platform: "google", owner: "me", account_id: MINE });
  });

  it("signs a new account in on the chosen platform", async () => {
    const calls = backend();
    render(<BotAccounts />);
    await screen.findByText("Work Google");
    fireEvent.change(screen.getByRole("combobox", { name: "Bot account platform" }), { target: { value: "microsoft" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in a bot account" }));
    await waitFor(() => expect(postBody(calls)).toEqual({ platform: "microsoft" }));
  });

  it("the admin account signs accounts in for the company and manages company accounts", async () => {
    const calls = backend({ admin: true });
    render(<BotAccounts />);
    await screen.findByText("Saved for the company.");
    const company = screen.getByText("Company Zoom").closest("[data-bot-account]") as HTMLElement;
    expect(within(company).getByRole("button", { name: "Delete Company Zoom" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Sign in a bot account" }));
    await waitFor(() => expect(postBody(calls)).toEqual({ platform: "google", owner: "company" }));
  });

  it("deletes only after confirmation", async () => {
    const calls = backend();
    render(<BotAccounts />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete Work Google" }));
    expect(calls.some(([, i]) => i?.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Delete bot account" }));
    await waitFor(() => expect(screen.queryByText("Work Google")).toBeNull());
    expect(calls.some(([u, i]) => u === `/api/bot-accounts/${MINE}` && i?.method === "DELETE")).toBe(true);
  });

  it("says so when the deployment has no bot accounts", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 403 })));
    render(<BotAccounts />);
    expect(await screen.findByText("Bot accounts are not available on this deployment.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign in a bot account" })).toBeNull();
  });

  it("is a section of the Connections page", async () => {
    backend();
    render(<ConnectionsPanel />);
    fireEvent(window, new Event(CONNECTIONS_OPEN));
    expect(await screen.findByRole("region", { name: "Bot accounts" })).toBeTruthy();
  });
});

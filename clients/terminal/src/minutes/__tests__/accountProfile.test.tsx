import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/** The Company profile switch in the account menu (ADR-0049; founder spec 2026-10-13): an instance
 *  administrator sees "Switch to Company", a tab working as the company shows the company on the
 *  card and offers "Switch back to <person>", and everybody else sees no entry at all. */

import { AccountBadge } from "../AccountBadge";

function serve(me: unknown, active: unknown = { subject: "1", active: [] }) {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url: String(url), body: init.body as string | undefined });
    if (String(url).startsWith("/api/auth/me")) return new Response(JSON.stringify(me), { status: 200 });
    if (String(url).startsWith("/api/workspace/active")) return new Response(JSON.stringify(active), { status: 200 });
    if (String(url).startsWith("/api/auth/company")) return new Response("{}", { status: 500 });
    return new Response("{}", { status: 404 });
  }));
  return calls;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("account menu — the Company profile", () => {
  it("an instance administrator is offered Switch to Company", async () => {
    const calls = serve({ authenticated: true, user: { email: "founder@acme.example", name: "Founder" }, can_work_as_company: true });
    render(<AccountBadge />);
    await screen.findByText("Founder");
    fireEvent.click(screen.getByRole("button", { name: /Founder/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Switch to Company" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/auth/company" && c.body === JSON.stringify({ to: "company" }))).toBe(true));
    // The server refused here (500): the menu says so instead of reloading into nothing.
    await screen.findByRole("alert");
  });

  it("everybody else sees no Company entry", async () => {
    serve({ authenticated: true, user: { email: "someone@acme.example", name: "Someone" }, can_work_as_company: false });
    render(<AccountBadge />);
    await screen.findByText("Someone");
    fireEvent.click(screen.getByRole("button", { name: /Someone/ }));
    expect(screen.queryByRole("menuitem", { name: /Company/ })).toBeNull();
  });

  it("in Company mode the card names the company and offers the way back", async () => {
    serve(
      { authenticated: true, admin_account: true, user: { email: "company@acme.example" }, company: { person: { email: "founder@acme.example", name: "Founder" } } },
      { subject: "99", admin_account: true, active: [{ slug: "_global", name: "Acme Ltd" }] },
    );
    render(<AccountBadge />);
    await screen.findByText("Acme Ltd");
    expect(screen.getByText("Company")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Acme Ltd/ }));
    expect(screen.getByRole("menuitem", { name: "Switch back to Founder" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Switch to Company" })).toBeNull();
  });
});

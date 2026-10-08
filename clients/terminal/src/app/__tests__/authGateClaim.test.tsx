/** AuthGate after the company-layer gate was removed (founder ruling 2026-10-08: "let's remove
 *  global setup at all so that there is no need to setup global at all - let it be empty with no
 *  data - it's fine").
 *
 *  Two properties, both silent when they break:
 *    1. nobody signed in is held back for the state of `_global` — not even when an older admin-api
 *       still reports `global_setup: "missing"`;
 *    2. an instance with NO administrator still offers the claim, in place, to a session that
 *       already exists — an instance must still get an admin.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { AuthGate, gateVerdict } from "../AuthGate";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("gateVerdict", () => {
  it("an instance with an administrator is open", () => {
    expect(gateVerdict({ probed: true, adminExists: true })).toBe("open");
  });

  it("an instance with NO administrator offers the claim, not a refusal", () => {
    expect(gateVerdict({ probed: true, adminExists: false })).toBe("claim");
  });

  it("nothing renders until the probe has settled", () => {
    expect(gateVerdict({ probed: false, adminExists: true })).toBe("pending");
  });
});

/** Route AuthGate's mount probes. `global_setup` is what an admin-api from before this change still
 *  sends; the terminal must ignore it. */
function stubGate(opts: {
  isAdmin?: boolean | null;
  email?: string;
  adminExists?: boolean;
  instanceFails?: boolean;
  onClaim?: () => Response;
}) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    calls.push(`${init?.method || "GET"} ${u}`);
    if (u.startsWith("/api/auth/me")) {
      return new Response(JSON.stringify({
        authenticated: true,
        is_admin: opts.isAdmin === undefined ? false : opts.isAdmin,
        user: { email: opts.email ?? "someone@example.com" },
      }), { status: 200 });
    }
    if (u.startsWith("/api/auth/providers")) return new Response("{}", { status: 200 });
    if (u.startsWith("/api/auth/instance")) {
      if (opts.instanceFails) throw new Error("ECONNREFUSED");
      return new Response(JSON.stringify({ admin_exists: opts.adminExists ?? true, global_setup: "missing" }), { status: 200 });
    }
    if (u.startsWith("/api/auth/claim-admin")) return opts.onClaim?.() ?? new Response(JSON.stringify({ success: true, url: "/" }), { status: 200 });
    return new Response("{}", { status: 200 });
  }));
  return calls;
}

describe("AuthGate — no instance is gated on `_global`", () => {
  it("a NON-admin gets the terminal while `_global` is unwritten", async () => {
    stubGate({ isAdmin: false });
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByText("the app");
    expect(screen.queryByText(/being set up by its administrator/)).toBeNull();
  });

  it("the admin gets the terminal too — no setup step in front of it", async () => {
    stubGate({ isAdmin: true });
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByText("the app");
  });

  it("an unreachable instance probe still renders the terminal", async () => {
    stubGate({ isAdmin: false, instanceFails: true });
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByText("the app");
  });
});

describe("AuthGate — the first admin can still be claimed", () => {
  it("offers the claim, spelling out what claiming means BEFORE the button", async () => {
    stubGate({ adminExists: false, email: "dmitry@vexa.ai" });
    render(<AuthGate><div>the app</div></AuthGate>);

    await screen.findByTestId("claim-instance");
    expect(screen.getByText("This Vexa has no administrator yet.")).toBeTruthy();
    expect(screen.getByText(/no second administrator to undo this/)).toBeTruthy();
    // nothing about writing a company layer first — that step is gone
    expect(screen.queryByText(/company layer/)).toBeNull();
    expect(screen.getByRole("button", { name: "Claim this instance" })).toBeTruthy();
    expect(screen.queryByText("the app")).toBeNull();
    expect(screen.getByRole("button", { name: "Not you? Sign out" })).toBeTruthy();
  });

  it("POSTs the claim to the server, which is the only thing that may grant it", async () => {
    const calls = stubGate({ adminExists: false });
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByTestId("claim-instance");

    fireEvent.click(screen.getByRole("button", { name: "Claim this instance" }));
    await waitFor(() => expect(calls.some((c) => c === "POST /api/auth/claim-admin")).toBe(true));
  });

  it("surfaces a refused claim instead of pretending it worked", async () => {
    stubGate({
      adminExists: false,
      onClaim: () => new Response(JSON.stringify({ error: "Could not claim this instance — try again in a moment." }), { status: 503 }),
    });
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByTestId("claim-instance");

    fireEvent.click(screen.getByRole("button", { name: "Claim this instance" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain("Could not claim this instance");
  });
});

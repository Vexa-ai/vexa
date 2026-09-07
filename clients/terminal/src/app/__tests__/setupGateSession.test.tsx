/** The company-layer gate as a SESSION check, not a door check.
 *
 *  ⚠ THE DEFECT THIS FILE PINS DOWN (observed live 2026-09-02, 08:48Z). The gate's refusals lived
 *  only in /api/auth/{login,redeem} and the OAuth callback. A browser holding a session minted
 *  BEFORE the gate existed never touches any of them again, so on a fully gated instance a
 *  non-admin got the whole terminal and a personal chat with the ordinary greeting. Nothing was
 *  broken; nothing had asked.
 *
 *  Two properties matter and both are silent when they break:
 *    1. the verdict is evaluated on EVERY page load, and
 *    2. a refused subject never renders `children` — the workbench mounts chats and fires dispatches
 *       on mount, and SetupGate (inside AuthGate) starts polling, so "render it and hide it" is not
 *       a refusal at all.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { AuthGate, setupGateVerdict } from "../AuthGate";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("setupGateVerdict — the four rows", () => {
  const base = { probed: true, globalSetup: "missing" as const, adminExists: true, isAdmin: false };

  it("row 1 — the layer is written: anyone gets the terminal", () => {
    expect(setupGateVerdict({ ...base, globalSetup: "completed", isAdmin: false })).toBe("open");
  });

  it("row 2 — gate up, this subject IS the admin: the terminal (SetupGate takes it from here)", () => {
    expect(setupGateVerdict({ ...base, isAdmin: true })).toBe("open");
  });

  it("row 3 — gate up, admin exists, this is somebody else: refused", () => {
    expect(setupGateVerdict({ ...base, isAdmin: false })).toBe("refused");
  });

  it("row 4 — gate up, NO admin yet: the claim screen, not a refusal", () => {
    // The founder's own case on 2026-09-02: a live session predating the gate, on an unclaimed
    // instance. Refusing here is a dead end — the sign-in doors that would otherwise claim the role
    // are behind him and a cookie never walks through one twice.
    expect(setupGateVerdict({ ...base, adminExists: false, isAdmin: false })).toBe("claim");
    // …and it stays a claim even if the oracle could not say who they are.
    expect(setupGateVerdict({ ...base, adminExists: false, isAdmin: null })).toBe("claim");
  });

  it("nothing renders until BOTH probes have settled", () => {
    // Rendering the workbench "for now" and retracting it a moment later is the same defect with a
    // shorter duration — the chats have already mounted by then.
    expect(setupGateVerdict({ ...base, probed: false })).toBe("pending");
  });

  it("an oracle that cannot say who this is does NOT produce a refusal", () => {
    // is_admin is three-valued. Collapsing null to false shows the ADMIN a lockout screen because
    // admin-api blinked; the closed half of the gate is server-side and loses nothing by this.
    expect(setupGateVerdict({ ...base, isAdmin: null })).toBe("open");
  });
});

/** Route both of AuthGate's mount probes. */
function stubGate(opts: {
  isAdmin?: boolean | null;
  email?: string;
  adminExists?: boolean;
  globalSetup?: "completed" | "missing";
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
      return new Response(JSON.stringify({
        admin_exists: opts.adminExists ?? true,
        global_setup: opts.globalSetup ?? "missing",
      }), { status: 200 });
    }
    if (u.startsWith("/api/auth/claim-admin")) return opts.onClaim?.() ?? new Response(JSON.stringify({ success: true }), { status: 200 });
    if (u.startsWith("/api/auth/logout")) return new Response("{}", { status: 200 });
    return new Response("{}", { status: 200 });
  }));
  return calls;
}

describe("AuthGate — an old cookie no longer walks past the gate", () => {
  it("REFUSES a non-admin and never renders the workbench", async () => {
    stubGate({ isAdmin: false, email: "dmitry+dailies@vexa.ai" });
    render(<AuthGate><div>the app</div></AuthGate>);

    await screen.findByTestId("setup-refused");
    expect(screen.getByText("This Vexa is being set up by its administrator.")).toBeTruthy();
    expect(screen.getByText(/dmitry\+dailies@vexa\.ai/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
    // The property that actually matters: children never mounted.
    expect(screen.queryByText("the app")).toBeNull();
  });

  it("lets the ADMIN through to the workbench (SetupGate runs the wizard beneath)", async () => {
    stubGate({ isAdmin: true });
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByText("the app");
    expect(screen.queryByTestId("setup-refused")).toBeNull();
  });

  it("lets everyone through once the layer is written", async () => {
    stubGate({ isAdmin: false, globalSetup: "completed" });
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByText("the app");
  });

  it("an unreachable instance probe FAILS OPEN — the terminal renders", async () => {
    // Unchanged direction: a blip must not lock a working instance out of itself. agent-api holds
    // the closed half, so a browser that renders on a blip can still do nothing.
    stubGate({ isAdmin: false, instanceFails: true });
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByText("the app");
  });
});

describe("AuthGate — the claim screen is the way out of the dead end", () => {
  it("offers the claim, spelling out what claiming means BEFORE the button", async () => {
    stubGate({ adminExists: false, isAdmin: false, email: "dmitry@vexa.ai" });
    render(<AuthGate><div>the app</div></AuthGate>);

    await screen.findByTestId("claim-instance");
    expect(screen.getByText("This Vexa has no administrator yet.")).toBeTruthy();
    // The consequence is stated, not implied: this is the highest-privilege act the product offers.
    expect(screen.getByText(/every agent working here carries what you write/)).toBeTruthy();
    expect(screen.getByText(/no second administrator to undo this/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Claim this instance" })).toBeTruthy();
    // It is not a dismissible notice — there is no way past it except claiming or signing out.
    expect(screen.queryByText("the app")).toBeNull();
    expect(screen.getByRole("button", { name: "Not you? Sign out" })).toBeTruthy();
  });

  it("POSTs the claim to the server, which is the only thing that may grant it", async () => {
    const calls = stubGate({ adminExists: false, isAdmin: false });
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByTestId("claim-instance");

    fireEvent.click(screen.getByRole("button", { name: "Claim this instance" }));
    await waitFor(() => expect(calls.some((c) => c === "POST /api/auth/claim-admin")).toBe(true));
  });

  it("surfaces a refused claim instead of pretending it worked", async () => {
    stubGate({
      adminExists: false,
      isAdmin: false,
      onClaim: () => new Response(JSON.stringify({ error: "Could not claim this instance — try again in a moment." }), { status: 503 }),
    });
    render(<AuthGate><div>the app</div></AuthGate>);
    await screen.findByTestId("claim-instance");

    fireEvent.click(screen.getByRole("button", { name: "Claim this instance" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain("Could not claim this instance");
  });
});

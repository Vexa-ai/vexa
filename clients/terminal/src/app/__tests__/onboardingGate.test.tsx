/** THE LANDING RULE — what exists on an admin's first render of a fresh instance.
 *
 *  Founder, watching a real first admin click (2026-09-02): "this is what I get from the first
 *  admin click — it should want to setup global here." What he got instead was a Personal chat
 *  opened on the ordinary greeting ("I'm your agent here… paste a meeting link") on an instance
 *  that could not join a meeting, could not send a mail, and served nobody.
 *
 *  This gate is what fired that greeting, so this is where the rule is pinned. Two halves, and the
 *  second is the one that rots quietly:
 *    • while the company layer is missing, NOTHING happens here — no workspace init, no seed;
 *    • the durable onboarded flag is NOT set either, so the personal onboarding is DEFERRED to the
 *      first load after the instance opens rather than silently spent on a load that suppressed it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import React from "react";

import { OnboardingGate, shouldSeedOnboarding, __resetOnboardingBootstrap } from "../OnboardingGate";
import { ONBOARDING_SEED_EVENT } from "../../canvas/actions";

const EMAIL = "admin@acme.test";
const FLAG = `vexa.terminal.onboarded.${EMAIL}`;

beforeEach(() => {
  __resetOnboardingBootstrap();
  try { localStorage.clear(); } catch { /* ignore */ }
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("shouldSeedOnboarding — the fail direction is the decision", () => {
  it("the layer is written → seed", () => expect(shouldSeedOnboarding("completed")).toBe(true));
  it("the layer is missing → do not seed", () => expect(shouldSeedOnboarding("missing")).toBe(false));
  it("the probe could not answer → seed anyway", () => {
    // Failing closed on a blip kills onboarding for every ordinary new user of a healthy instance,
    // permanently, because they arrive exactly once. Failing open costs one stray greeting on an
    // instance that is about to be set up.
    expect(shouldSeedOnboarding(null)).toBe(true);
  });
});

/** Route the three calls the gate makes: who am I, is the layer written, materialise the workspace. */
function stub(globalSetup: "completed" | "missing" | "throw") {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    calls.push(`${init?.method || "GET"} ${u}`);
    if (u.startsWith("/api/auth/me")) {
      return new Response(JSON.stringify({ authenticated: true, user: { email: EMAIL } }), { status: 200 });
    }
    if (u.includes("/api/global/state")) {
      if (globalSetup === "throw") throw new Error("ECONNREFUSED");
      return new Response(JSON.stringify({
        global_setup: globalSetup, company: null,
        present: [], missing_files: [], reasons: [],
      }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  }));
  return calls;
}

/** The gate awaits three round trips and then seeds on a 600ms timer. REAL timers, deliberately:
 *  fake ones would have to be advanced from outside a promise chain whose length is an
 *  implementation detail, and a test that advances too little reports "it did not seed" — which is
 *  exactly the assertion this file exists to make, and it would be lying. One real second buys a
 *  test that cannot pass for the wrong reason. */
const settle = () => new Promise((r) => setTimeout(r, 1000));

describe("OnboardingGate on an instance whose company layer is MISSING", () => {
  it("fires no greeting, materialises no workspace, and spends no onboarding", async () => {
    const seeds = vi.fn();
    window.addEventListener(ONBOARDING_SEED_EVENT, seeds);
    const calls = stub("missing");
    render(<OnboardingGate><div data-testid="workbench" /></OnboardingGate>);
    await settle();

    expect(seeds).not.toHaveBeenCalled();
    // `initWorkspace` is the other half of "what exists on first render" — no personal workspace
    // is materialised while the instance serves nobody.
    expect(calls.some((c) => c.includes("/api/workspace"))).toBe(false);
    // …and the ONE fact that makes this a deferral rather than a loss.
    expect(localStorage.getItem(FLAG)).toBeNull();
    window.removeEventListener(ONBOARDING_SEED_EVENT, seeds);
  });

  it("still renders the children — it suppresses the seed, it is not a second gate", async () => {
    stub("missing");
    const { getByTestId } = render(<OnboardingGate><div data-testid="workbench" /></OnboardingGate>);
    await settle();
    expect(getByTestId("workbench")).toBeTruthy();
  });
});

describe("OnboardingGate once the instance is open", () => {
  it("seeds the greeting and marks the user onboarded", async () => {
    const seeds = vi.fn();
    window.addEventListener(ONBOARDING_SEED_EVENT, seeds);
    stub("completed");
    render(<OnboardingGate><div data-testid="workbench" /></OnboardingGate>);
    await settle();

    expect(seeds).toHaveBeenCalled();
    expect(localStorage.getItem(FLAG)).toBe("1");
    window.removeEventListener(ONBOARDING_SEED_EVENT, seeds);
  });

  it("an unreachable state probe does not cost a new user their onboarding", async () => {
    const seeds = vi.fn();
    window.addEventListener(ONBOARDING_SEED_EVENT, seeds);
    stub("throw");
    render(<OnboardingGate><div data-testid="workbench" /></OnboardingGate>);
    await settle();

    expect(seeds).toHaveBeenCalled();
    window.removeEventListener(ONBOARDING_SEED_EVENT, seeds);
  });

  it("an already-onboarded user never reaches the probe at all", async () => {
    localStorage.setItem(FLAG, "1");
    const calls = stub("completed");
    render(<OnboardingGate><div data-testid="workbench" /></OnboardingGate>);
    await settle();
    expect(calls.some((c) => c.includes("/api/global/state"))).toBe(false);
  });
});

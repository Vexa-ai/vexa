/** THE LANDING RULE — what exists on a person's first render, the admin's included.
 *
 *  ── NO COMPANY-LAYER GATE (founder ruling 2026-10-08) ───────────────────────────────────────────
 *  "let's remove global setup at all so that there is no need to setup global at all - let it be
 *  empty with no data - it's fine." This gate used to hold onboarding back while `_global` was
 *  unwritten. It no longer asks about `_global` at all: everybody is seeded on their first load.
 *
 *  ── AND IT NEVER GREETS (F36) ───────────────────────────────────────────────────────────────────
 *  A new chat shows an empty composer and nothing else, so the first assertion below is that NO
 *  chat event of any kind leaves this gate. What it does is the half that is not a message:
 *  materialise the workspace, once, behind a durable per-user flag.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import React from "react";

import { OnboardingGate, __resetOnboardingBootstrap } from "../OnboardingGate";

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

/** Route the calls the gate makes: who am I, materialise the workspace. `/api/global/state` answers
 *  as an instance whose company layer is EMPTY, so a regression that starts asking again would read
 *  the old gate's "missing" and stop seeding — which is what the first test catches. */
function stub() {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    calls.push(`${init?.method || "GET"} ${u}`);
    if (u.startsWith("/api/auth/me")) {
      return new Response(JSON.stringify({ authenticated: true, user: { email: EMAIL } }), { status: 200 });
    }
    if (u.includes("/api/global/state")) {
      return new Response(JSON.stringify({ global_setup: "missing", present: [], missing_files: [] }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  }));
  return calls;
}

/** REAL timers, deliberately: fake ones would have to be advanced from outside a promise chain
 *  whose length is an implementation detail, and a test that advances too little reports "it did
 *  not seed" — exactly the assertion this file exists to make. */
const settle = () => new Promise((r) => setTimeout(r, 1000));

/** F36 — the gate never speaks. Asserted by listening for EVERY `vexa:terminal:*` event that ever
 *  carried a greeting rather than for one name. */
function listenForChatEvents(): { fired: string[]; stop: () => void } {
  const fired: string[] = [];
  const names = ["vexa:terminal:onboarding-seed", "vexa:terminal:company-layer", "vexa:terminal:ask-chat"];
  const on = (e: Event) => { fired.push(e.type); };
  for (const n of names) window.addEventListener(n, on);
  return { fired, stop: () => { for (const n of names) window.removeEventListener(n, on); } };
}

describe("OnboardingGate on an instance whose `_global` is empty", () => {
  it("seeds onboarding anyway — materialises the workspace and marks the user onboarded", async () => {
    const calls = stub();
    render(<OnboardingGate><div data-testid="workbench" /></OnboardingGate>);
    await settle();

    expect(calls.some((c) => c.includes("/api/workspace"))).toBe(true);
    expect(localStorage.getItem(FLAG)).toBe("1");
    // …and it never asks about the company layer: there is no state of `_global` that defers this.
    expect(calls.some((c) => c.includes("/api/global/state"))).toBe(false);
  });

  it("says nothing while it does (F36)", async () => {
    const heard = listenForChatEvents();
    stub();
    render(<OnboardingGate><div data-testid="workbench" /></OnboardingGate>);
    await settle();
    expect(heard.fired).toEqual([]);
    heard.stop();
  });

  it("renders the children", async () => {
    stub();
    const { getByTestId } = render(<OnboardingGate><div data-testid="workbench" /></OnboardingGate>);
    await settle();
    expect(getByTestId("workbench")).toBeTruthy();
  });

  it("an already-onboarded user is not seeded again", async () => {
    localStorage.setItem(FLAG, "1");
    const calls = stub();
    render(<OnboardingGate><div data-testid="workbench" /></OnboardingGate>);
    await settle();
    expect(calls.some((c) => c.includes("/api/workspace"))).toBe(false);
  });
});

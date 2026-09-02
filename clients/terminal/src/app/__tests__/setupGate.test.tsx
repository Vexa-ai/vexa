/** SetupGate — the admin first-run wizard's gating behavior, now THREE steps and FOUR phases.
 *
 *  The wizard must show ONLY to an admin on an instance whose setup is incomplete; everyone else
 *  falls straight through to the workbench (children). The probe is /api/admin/settings/setup —
 *  404 (non-admin) → null.
 *
 *  What step 3 added (founder ruling 2026-09-02, the company layer): the wizard can be in a HANDOFF
 *  phase, where the workbench is mounted and running the setup conversation and the wizard is a
 *  small card polling /api/global/state. Two things about that are worth pinning down here, because
 *  both are silent when they break:
 *    • a RELOAD mid-conversation must resume as a handoff, not restart the wizard at step 1,
 *    • `setup.completed` is written by exactly ONE thing — the card's Continue, which only appears
 *      after the SERVER says the layer is complete.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { SetupGate, setupResumePhase, setupResumeStep, shouldShowSetup } from "../SetupGate";

beforeEach(() => {
  // The step-3 hand-off ends in a real navigation; jsdom cannot perform one. Neutralise it where the
  // runtime allows (window.location is [Unforgeable] in some jsdom versions) so the test asserts the
  // PERSIST, which is the half that must not be lost.
  try { vi.spyOn(window.location, "assign").mockImplementation(() => {}); } catch { /* left as a jsdom warning */ }
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

type Stub = {
  setup?: { status: number; value?: Record<string, string> };
  global?: Record<string, unknown>;
};

/** One fetch double for both probes the component uses: the platform-settings `setup` key and the
 *  company layer's live state. Returns the recorded calls so a test can assert what was PERSISTED. */
function stubProbes(s: Stub) {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      calls.push({ url: u, method: init?.method || "GET", body: init?.body as string });
      if (u.includes("/api/admin/settings/setup")) {
        const cfg = s.setup ?? { status: 200, value: {} };
        if (init?.method === "PUT") return new Response(JSON.stringify({ key: "setup", value: {} }), { status: 200 });
        if (cfg.status === 404) return new Response(null, { status: 404 });
        return new Response(JSON.stringify({ key: "setup", value: cfg.value ?? {} }), { status: 200 });
      }
      if (u.includes("/api/global/state")) {
        return new Response(JSON.stringify(s.global ?? {
          global_setup: "missing", company: null, present: [],
          missing_files: ["README.md"], reasons: ["_global has no README.md yet."],
        }), { status: 200 });
      }
      // the wizard's mount-time model/transcription detection — irrelevant to gating, keep it quiet
      return new Response(JSON.stringify({ ok: false, summary: "stub" }), { status: 200 });
    }),
  );
  return calls;
}

describe("shouldShowSetup", () => {
  it("null (non-admin probe 404) → hidden", () => expect(shouldShowSetup(null)).toBe(false));
  it("completed → hidden", () => expect(shouldShowSetup({ completed: "true" })).toBe(false));
  it("fresh / partial → shown", () => {
    expect(shouldShowSetup({})).toBe(true);
    expect(shouldShowSetup({ models: "done" })).toBe(true);
  });
});

describe("setupResumePhase — the three-way decision a reload makes", () => {
  it("non-admin → hidden", () => expect(setupResumePhase(null)).toBe("hidden"));
  it("completed → hidden", () => expect(setupResumePhase({ completed: "true" })).toBe("hidden"));
  it("fresh → wizard", () => expect(setupResumePhase({})).toBe("wizard"));
  it("mid-wizard → wizard", () => expect(setupResumePhase({ models: "done" })).toBe("wizard"));
  it("already handed off → handoff, NOT a restart at step 1", () => {
    expect(setupResumePhase({ models: "done", transcription: "done", global: "handoff" })).toBe("handoff");
  });
  it("completed WINS over a stale handoff marker", () => {
    // Otherwise an instance that finished setup would put the corner card back on every load.
    expect(setupResumePhase({ global: "handoff", completed: "true" })).toBe("hidden");
  });
});

describe("setupResumeStep", () => {
  it("nothing recorded → step 1", () => expect(setupResumeStep({})).toBe(1));
  it("models recorded → step 2", () => expect(setupResumeStep({ models: "skipped" })).toBe(2));
  it("both recorded → step 3", () => expect(setupResumeStep({ models: "done", transcription: "done" })).toBe(3));
});

describe("SetupGate", () => {
  it("non-admin falls through to the workbench", async () => {
    stubProbes({ setup: { status: 404 } });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(screen.getByTestId("workbench")).toBeTruthy());
  });

  it("completed instance falls through", async () => {
    stubProbes({ setup: { status: 200, value: { completed: "true" } } });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(screen.getByTestId("workbench")).toBeTruthy());
  });

  it("admin on a fresh instance gets the wizard, not the workbench", async () => {
    stubProbes({ setup: { status: 200, value: {} } });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(screen.getByText("How should the agent think?")).toBeTruthy());
    expect(screen.queryByTestId("workbench")).toBeNull();
  });

  it("probe failure fails SAFE — workbench renders", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(screen.getByTestId("workbench")).toBeTruthy());
  });
});

describe("step 3 — the company layer", () => {
  it("is reached on resume, and is NOT skippable", async () => {
    stubProbes({ setup: { status: 200, value: { models: "done", transcription: "done" } } });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(screen.getByText("Who is this company?")).toBeTruthy());
    // Steps 1 and 2 carry a "Skip for now"; step 3 says on screen that it cannot be skipped rather
    // than hiding an affordance the admin would go hunting for.
    expect(screen.queryByText("Skip for now")).toBeNull();
    expect(screen.getByText(/can’t be skipped/)).toBeTruthy();
  });

  it("hands off by PERSISTING the phase before it navigates", async () => {
    // Order matters: the navigation destroys the component, so a write started after it may never
    // be sent — and the admin would be thrown back to step 1 on their next reload.
    const calls = stubProbes({ setup: { status: 200, value: { models: "done", transcription: "done" } } });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(screen.getByText("Write it with the agent")).toBeTruthy());

    fireEvent.click(screen.getByText("Write it with the agent"));
    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT" && c.url.includes("/api/admin/settings/setup"));
      expect(put).toBeDefined();
      expect(JSON.parse(put!.body || "{}")).toEqual({ global: "handoff" });
    });
  });
});

describe("the handoff card", () => {
  it("renders the workbench underneath and withholds Continue while the layer is missing", async () => {
    stubProbes({
      setup: { status: 200, value: { models: "done", transcription: "done", global: "handoff" } },
      global: { global_setup: "missing", company: null, present: ["README.md"],
        missing_files: ["PRINCIPLES.md"], reasons: ["_global is missing PRINCIPLES.md."] },
    });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);

    // The whole point of the handoff phase: the chat is live UNDER the card.
    await waitFor(() => expect(screen.getByTestId("workbench")).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("global-gate-card")).toBeTruthy());
    // The server's own sentence, rendered rather than paraphrased.
    await waitFor(() => expect(screen.getByText("_global is missing PRINCIPLES.md.")).toBeTruthy());
    expect(screen.queryByText("Continue")).toBeNull();
  });

  it("offers Continue once the SERVER says completed, and only then writes setup.completed", async () => {
    const calls = stubProbes({
      setup: { status: 200, value: { models: "done", transcription: "done", global: "handoff" } },
      global: { global_setup: "completed", company: "Acme GmbH", present: [], missing_files: [], reasons: [] },
    });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);

    await waitFor(() => expect(screen.getByText(/Acme GmbH/)).toBeTruthy());
    // Nothing has claimed setup is finished yet — the human still has to press the button.
    expect(calls.some((c) => c.method === "PUT" && (c.body || "").includes("completed"))).toBe(false);

    fireEvent.click(screen.getByText("Continue"));
    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT" && (c.body || "").includes("completed"));
      expect(JSON.parse(put!.body || "{}")).toEqual({ completed: "true" });
    });
    // …and the card is gone, leaving the workbench alone.
    await waitFor(() => expect(screen.queryByTestId("global-gate-card")).toBeNull());
    expect(screen.getByTestId("workbench")).toBeTruthy();
  });
});

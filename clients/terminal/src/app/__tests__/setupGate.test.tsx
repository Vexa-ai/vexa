/** SetupGate — the admin first-run wizard's gating behavior. The wizard must show ONLY to an
 *  admin on an instance whose setup is incomplete; everyone else falls straight through to the
 *  workbench (children). The probe is /api/admin/settings/setup — 404 (non-admin) → null. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { SetupGate, shouldShowSetup } from "../SetupGate";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubSetupProbe(response: { status: number; value?: Record<string, string> }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (String(url).includes("/api/admin/settings/setup")) {
        if (response.status === 404) return new Response(null, { status: 404 });
        return new Response(JSON.stringify({ key: "setup", value: response.value ?? {} }), { status: 200 });
      }
      // the wizard's mount-time model detection — irrelevant to gating, keep it quiet
      return new Response(JSON.stringify({ ok: false, summary: "stub" }), { status: 200 });
    }),
  );
}

describe("shouldShowSetup", () => {
  it("null (non-admin probe 404) → hidden", () => expect(shouldShowSetup(null)).toBe(false));
  it("completed → hidden", () => expect(shouldShowSetup({ completed: "true" })).toBe(false));
  it("fresh / partial → shown", () => {
    expect(shouldShowSetup({})).toBe(true);
    expect(shouldShowSetup({ models: "done" })).toBe(true);
  });
});

describe("SetupGate", () => {
  it("non-admin falls through to the workbench", async () => {
    stubSetupProbe({ status: 404 });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(screen.getByTestId("workbench")).toBeTruthy());
  });

  it("completed instance falls through", async () => {
    stubSetupProbe({ status: 200, value: { completed: "true" } });
    render(<SetupGate><div data-testid="workbench" /></SetupGate>);
    await waitFor(() => expect(screen.getByTestId("workbench")).toBeTruthy());
  });

  it("admin on a fresh instance gets the wizard, not the workbench", async () => {
    stubSetupProbe({ status: 200, value: {} });
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

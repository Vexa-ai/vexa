/** Settings → Models: "Default model" (ADR-0043) — a selector backed by the deployment's catalog.
 *
 *  The options are exactly the models agent-api lists for this person (never typed), "inherit"
 *  defers to the organisation's default; the admin's global form sets the organisation's own. A
 *  save sends `default_model` and nothing else; a deployment with no catalog shows no such field.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { ModelsSection } from "../settings";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const CATALOG = {
  models: [
    { id: "qwen3-32b", display_name: "Qwen 3 32B (self-hosted)", provider: "lab-vllm", adapter: "openai_compatible",
      harness: "openai-agent", capabilities: { tool_calling: true, streaming: true, context_tokens: 32768 },
      access: "everyone", default: true },
    { id: "claude", display_name: "Claude (subscription)", provider: "anthropic", adapter: "anthropic",
      harness: "claude-code", capabilities: { tool_calling: true, streaming: true, context_tokens: null },
      access: "everyone", default: false },
  ],
  default: "qwen3-32b",
};

function stub(catalog: unknown, { admin = false } = {}) {
  const puts: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      puts.push({ url: u, body });
      return new Response(JSON.stringify(u.startsWith("/api/admin") ? { key: "models", value: body } : body), { status: 200 });
    }
    if (u.startsWith("/api/models/catalog")) return new Response(JSON.stringify(catalog), { status: 200 });
    if (u === "/api/user/models") return new Response(JSON.stringify({ mode: null, default_model: null }), { status: 200 });
    if (u === "/api/admin/settings/models") {
      return admin ? new Response(JSON.stringify({ key: "models", value: {} }), { status: 200 })
                   : new Response("{}", { status: 404 });
    }
    return new Response("{}", { status: 404 });
  }));
  return puts;
}

const selects = () => screen.queryAllByRole("combobox").filter((s) =>
  Array.from((s as HTMLSelectElement).options).some((o) => o.value === "qwen3-32b"));

describe("the Default model setting", () => {
  it("lists exactly the catalog's models and saves default_model alone", async () => {
    const puts = stub(CATALOG);
    render(<ModelsSection />);
    await waitFor(() => expect(selects().length).toBe(1));
    const sel = selects()[0] as HTMLSelectElement;
    expect(Array.from(sel.options).map((o) => o.value)).toEqual(["", "qwen3-32b", "claude"]);
    expect(sel.options[0].textContent).toBe("Organisation default");
    fireEvent.change(sel, { target: { value: "claude" } });
    fireEvent.click(screen.getAllByText("Save")[0]);
    await waitFor(() => expect(puts.length).toBe(1));
    expect(puts[0]).toEqual({ url: "/api/user/models", body: { default_model: "claude" } });
  });

  it("gives the admin the organisation's default too", async () => {
    stub(CATALOG, { admin: true });
    render(<ModelsSection />);
    await waitFor(() => expect(selects().length).toBe(2));
    expect((selects()[1] as HTMLSelectElement).options[0].textContent).toBe("Catalog default");
  });

  it("shows no such field on a deployment with no catalog", async () => {
    stub({ models: [], default: null });
    render(<ModelsSection />);
    await screen.findAllByText("Save");
    expect(selects()).toEqual([]);
    expect(screen.queryByText("Default model")).toBeNull();
  });
});

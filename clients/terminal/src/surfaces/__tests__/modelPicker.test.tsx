/** The model picker (ADR-0042) — a chat runs on the model its person picked.
 *
 *  Pinned here: the picker renders exactly the list agent-api sends for this person (role
 *  visibility is the server's — an admins-only model is simply absent), nothing at all on a
 *  deployment with no catalog, a pick posts to `/api/chat/model` for THIS chat, a refused pick shows
 *  the server's sentence and keeps the old pick, and "use for new chats" writes the person's
 *  Settings → Models `default_model`. No endpoint and no credential ever reaches this component.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { ModelPicker } from "../ModelPicker";
import type { ModelList } from "../modelsApi";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const MEMBER: ModelList = {
  models: [
    { id: "qwen3-32b", display_name: "Qwen 3 32B (self-hosted)", provider: "lab-vllm", adapter: "openai_compatible",
      harness: "openai-agent", capabilities: { tool_calling: true, streaming: true, context_tokens: 32768 },
      access: "everyone", default: true },
    { id: "claude", display_name: "Claude (subscription)", provider: "anthropic", adapter: "anthropic",
      harness: "claude-code", capabilities: { tool_calling: true, streaming: true, context_tokens: null },
      access: "everyone", default: false },
  ],
  default: "qwen3-32b",
  selected: null,
};

type Call = { url: string; method: string; body?: unknown };

function stub(list: ModelList, answers: Record<string, () => Response> = {}) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method || "GET").toUpperCase();
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const key = `${method} ${url.split("?")[0]}`;
    if (answers[key]) return answers[key]();
    if (key === "GET /api/models/catalog") return new Response(JSON.stringify(list), { status: 200 });
    if (key === "POST /api/chat/model") {
      const b = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({ ok: true, session: b.session, model: b.model || null, changed: true }), { status: 200 });
    }
    if (key === "PUT /api/user/models") return new Response(JSON.stringify({ default_model: "claude" }), { status: 200 });
    return new Response("{}", { status: 404 });
  }));
  return calls;
}

const chip = () => screen.findByRole("button", { name: "Model for this chat" });

describe("the model picker", () => {
  it("renders nothing on a deployment with no catalog", async () => {
    const calls = stub({ models: [], default: null, selected: null });
    const { container } = render(<ModelPicker session="s1" />);
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].url).toBe("/api/models/catalog?session=s1");
    expect(container.querySelector("[data-model-picker]")).toBeNull();
  });

  it("shows the chat's model — its default until picked — and exactly the models it was sent", async () => {
    stub(MEMBER);
    render(<ModelPicker session="s1" />);
    expect((await chip()).textContent).toContain("Qwen 3 32B (self-hosted)");
    fireEvent.click(await chip());
    const items = screen.getAllByRole("menuitemradio");
    expect(items.map((i) => i.getAttribute("data-model-id"))).toEqual(["qwen3-32b", "claude"]);
    expect(items[0].getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("menu").textContent).toContain("32k");
    expect(document.body.innerHTML).not.toMatch(/10\.0\.0\.5|openrouter\.ai|sk-|secret_ref/);
  });

  it("a pick posts to this chat and becomes the chat's model", async () => {
    const calls = stub(MEMBER);
    render(<ModelPicker session="s1" />);
    fireEvent.click(await chip());
    fireEvent.click(screen.getByText("Claude (subscription)"));
    await waitFor(() => expect((screen.getByRole("button", { name: "Model for this chat" })).textContent)
      .toContain("Claude (subscription)"));
    expect(calls.find((c) => c.method === "POST")).toEqual(
      { url: "/api/chat/model", method: "POST", body: { session: "s1", model: "claude" } });
  });

  it("a refused pick shows the server's sentence and keeps the chat's model", async () => {
    stub(MEMBER, {
      "POST /api/chat/model": () => new Response(JSON.stringify({
        detail: "Claude (subscription) is open to admins only on this deployment. Pick another model for this chat.",
        fault: { source: "model-provider", kind: "not_permitted", provider: "anthropic", model: "claude", status: null },
      }), { status: 403 }),
    });
    render(<ModelPicker session="s1" />);
    fireEvent.click(await chip());
    fireEvent.click(screen.getByText("Claude (subscription)"));
    expect((await screen.findByRole("alert")).textContent).toBeTruthy();
    expect((await chip()).textContent).toContain("Qwen 3 32B (self-hosted)");
  });

  it("a pick that is no longer offered says so on the chip", async () => {
    stub({ ...MEMBER, selected: "retired-model" });
    render(<ModelPicker session="s1" />);
    expect((await chip()).textContent).toContain("Model unavailable");
  });

  it("follow-my-default clears the chat's pick; use-for-new-chats writes the person's default", async () => {
    const calls = stub({ ...MEMBER, selected: "claude" });
    render(<ModelPicker session="s1" />);
    fireEvent.click(await chip());
    fireEvent.click(screen.getByText("Use Claude (subscription) for new chats"));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")).toEqual(
      { url: "/api/user/models", method: "PUT", body: { default_model: "claude" } });
    fireEvent.click(await chip());
    fireEvent.click(screen.getByText(/Follow my default/));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").length).toBe(1));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ session: "s1", model: "" });
  });
});

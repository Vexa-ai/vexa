/** The model picker (ADR-0043) — a chat runs on the model its person picked.
 *
 *  Pinned here: the picker renders exactly the list agent-api sends for this person (role
 *  visibility is the server's — an admins-only model is simply absent), nothing at all on a
 *  deployment with no catalog, a pick posts to `/api/chat/model` for THIS chat, a refused pick shows
 *  the server's sentence and keeps the old pick, and "use for new chats" writes the person's
 *  Settings → Models `default_model`. No endpoint and no credential ever reaches this component.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, act } from "@testing-library/react";
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

// ── the effort selector (founder 2026-10-10): only for a model that lists effort levels ───────

const WITH_EFFORT: ModelList = {
  models: [
    { ...MEMBER.models[0], capabilities: { ...MEMBER.models[0].capabilities, reasoning_efforts: [], default_effort: null } },
    { ...MEMBER.models[1], capabilities: { ...MEMBER.models[1].capabilities,
      reasoning_efforts: ["low", "medium", "high", "xhigh", "max"], default_effort: "medium" } },
  ],
  default: "qwen3-32b",
  selected: null,
  selected_effort: null,
};

const effortSelect = () => document.querySelector("[data-effort-picker]") as HTMLSelectElement | null;

describe("the effort selector", () => {
  it("is absent while the chat's model has no effort control", async () => {
    stub(WITH_EFFORT);
    render(<ModelPicker session="s1" />);
    await chip();
    expect(effortSelect()).toBeNull();
  });

  it("appears beside the chip for a model that lists levels, on its default level", async () => {
    stub({ ...WITH_EFFORT, selected: "claude" });
    render(<ModelPicker session="s1" />);
    await chip();
    const sel = effortSelect()!;
    expect(sel).not.toBeNull();
    expect([...sel.options].map((o) => o.value)).toEqual(["low", "medium", "high", "xhigh", "max"]);
    expect(sel.value).toBe("medium");
  });

  it("posts the level with the chat's model, and shows the chat's own pick", async () => {
    const calls = stub({ ...WITH_EFFORT, selected: "claude", selected_effort: "high" });
    render(<ModelPicker session="s1" />);
    await chip();
    expect(effortSelect()!.value).toBe("high");
    fireEvent.change(effortSelect()!, { target: { value: "max" } });
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ session: "s1", model: "claude", effort: "max" });
  });

  it("a level the server refuses shows its typed fault's sentence", async () => {
    stub({ ...WITH_EFFORT, selected: "claude" }, {
      "POST /api/chat/model": () => new Response(JSON.stringify({
        detail: "Claude (subscription) offers the effort levels low, medium — not max. Pick one of those levels, or another model.",
        fault: { source: "model-provider", kind: "effort_unsupported", provider: "anthropic", model: "claude",
                 status: null, detail: "Claude (subscription) offers the effort levels low, medium — not max.",
                 remedy: "Pick one of those levels, or another model." } }), { status: 422 }),
    });
    render(<ModelPicker session="s1" />);
    await chip();
    fireEvent.change(effortSelect()!, { target: { value: "max" } });
    expect((await screen.findByRole("alert")).textContent).toMatch(/not max/);
  });

  it("goes away when the chat moves to a model with no effort control", async () => {
    stub({ ...WITH_EFFORT, selected: "claude" });
    render(<ModelPicker session="s1" />);
    fireEvent.click(await chip());
    fireEvent.click(document.querySelector('[data-model-id="qwen3-32b"]')!);
    await waitFor(() => expect(effortSelect()).toBeNull());
  });
});

// ── a new chat: the pick shows at once, whatever the catalog read still in flight says ─────────
//
// Live on app.dev: in a new chat, an effort picked before the first message was stored, but the
// dropdown went back to the default. Switching chats keeps the previous chat's list on screen while
// the new chat's catalog read is in flight; a pick made then went out with the PREVIOUS chat's
// model, and the read, landing after it, put the dropdown back on the default.

describe("an effort picked in a new chat", () => {
  it("shows the pick right after picking, and a catalog read that lands later does not undo it", async () => {
    const calls: Call[] = [];
    let releaseNewChat: (r: Response) => void = () => {};
    // the person's default is the model with effort levels; the previous chat pinned it at max
    const previous = { ...WITH_EFFORT, default: "claude", selected: "claude", selected_effort: "max" as const };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (method === "GET" && url.includes("session=s1")) return new Response(JSON.stringify(previous), { status: 200 });
      if (method === "GET") return new Promise<Response>((resolve) => { releaseNewChat = resolve; });
      const b = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({ ok: true, session: b.session, model: b.model || null,
                                           effort: b.effort || null, changed: true }), { status: 200 });
    }));
    // the new chat follows the person's default, which here is the model with effort levels
    const newChat = { ...WITH_EFFORT, default: "claude", selected: null, selected_effort: null };

    const { rerender } = render(<ModelPicker session="s1" />);
    await chip();
    expect(effortSelect()!.value).toBe("max");

    rerender(<ModelPicker session="new-chat" />);          // the new chat's read is still in flight
    await waitFor(() => expect(calls.filter((c) => c.method === "GET")).toHaveLength(2));
    fireEvent.change(effortSelect()!, { target: { value: "high" } });
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    // the pick is for THIS chat, on the model it follows — never the previous chat's pick
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ session: "new-chat", model: "", effort: "high" });
    await waitFor(() => expect(effortSelect()!.value).toBe("high"));

    // the read started before the pick lands now, with the chat as it was before it
    await act(async () => { releaseNewChat(new Response(JSON.stringify(newChat), { status: 200 })); });
    await new Promise((r) => setTimeout(r, 20));
    expect(effortSelect()!.value).toBe("high");
  });
});

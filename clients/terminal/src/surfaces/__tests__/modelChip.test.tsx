/** UI-DECISIONS 7f (founder 2026-10-16): the "1 2 3 4 5 fastest" buttons become ONE composer chip,
 *  "Opus 5.5 · Medium". Its menu: the five leading models by name, smartest first, with number keys;
 *  an Effort row (reasoning only, instant); a model switch mid-chat asks once, inline; "More models"
 *  lists the rest where there are more. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ModelChip } from "../usage/ModelChip";

const levels = { levels: [
  { level: 1, word: "fastest", model: "m-haiku" }, { level: 2, word: "fast", model: "m-flash" },
  { level: 3, word: "balanced", model: "m-sonnet" }, { level: 4, word: "thorough", model: "m-gpt" },
  { level: 5, word: "most capable", model: "m-opus" } ], chat: { level: 5, model: "m-opus" } };
const cap = (e: string[] = []) => ({ tool_calling: true, streaming: true, reasoning_efforts: e, default_effort: e.length ? "medium" : null });
const catalog = { default: "m-sonnet", selected: "m-opus", selected_effort: null, models: [
  { id: "m-opus", display_name: "Claude Opus 5.5 (subscription)", capabilities: cap(["low", "medium", "high"]) },
  { id: "m-gpt", display_name: "GPT-6", capabilities: cap() }, { id: "m-sonnet", display_name: "Sonnet 5", capabilities: cap() },
  { id: "m-flash", display_name: "Flash 3", capabilities: cap() }, { id: "m-haiku", display_name: "Haiku 5", capabilities: cap() },
  { id: "m-extra", display_name: "Llama 5", capabilities: cap() } ].map((m) => ({ provider: "x", adapter: "openrouter", harness: "x", access: "everyone", default: false, ...m })) };

let posts: { url: string; body: Record<string, unknown> }[] = [];
beforeEach(() => {
  posts = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (init?.method === "POST") { const body = JSON.parse(String(init.body)); posts.push({ url: u, body }); return new Response(JSON.stringify({ ok: true, ...body, changed: true }), { status: 200 }); }
    if (u.startsWith("/api/models/levels")) return new Response(JSON.stringify(levels));
    if (u.startsWith("/api/models/catalog")) return new Response(JSON.stringify(catalog));
    return new Response("{}");
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("ModelChip", () => {
  it("names the model short, with its source only as a hint, and its menu is portalled", async () => {
    render(<ModelChip session="s1" hasMessages={false} />);
    const chip = await screen.findByRole("button", { name: /Opus 5\.5 · Medium/ });
    expect(chip.textContent).toBe("Opus 5.5 · Medium");
    expect(chip.getAttribute("title")).toContain("subscription");
    fireEvent.click(chip);
    expect(screen.getByRole("menu").parentElement).toBe(document.body);
    expect(screen.getAllByRole("menuitemradio")[0].textContent).not.toContain("subscription");
  });
  it("is one chip naming the model and the effort", async () => {
    render(<ModelChip session="s1" hasMessages={false} />);
    expect(await screen.findByRole("button", { name: /Opus 5\.5 · Medium/ })).toBeTruthy();
    expect(screen.queryByRole("radio", { name: "1" })).toBeNull();
  });
  it("lists the five leading models smartest first, with number keys, and More models", async () => {
    render(<ModelChip session="s1" hasMessages={false} />);
    fireEvent.click(await screen.findByRole("button", { name: /Opus 5\.5/ }));
    const rows = screen.getAllByRole("menuitemradio").map((r) => r.textContent ?? "");
    expect(rows[0]).toMatch(/^1.*Opus 5\.5/);
    expect(rows[4]).toMatch(/^5.*Haiku 5/);
    expect(screen.getByText(/More models/)).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "3" });
    await waitFor(() => expect(posts.find((p) => p.url === "/api/chat/level")?.body).toMatchObject({ session: "s1", level: 3 }));
  });
  it("effort changes reasoning only, instantly", async () => {
    render(<ModelChip session="s1" hasMessages />);
    fireEvent.click(await screen.findByRole("button", { name: /Opus 5\.5/ }));
    fireEvent.click(screen.getByRole("button", { name: "High" }));
    await waitFor(() => expect(posts.find((p) => p.url === "/api/chat/model")?.body).toEqual({ session: "s1", model: "m-opus", effort: "high" }));
    expect(posts.some((p) => p.url === "/api/chat/level")).toBe(false);
  });
  it("a model switch mid-chat asks once, inline", async () => {
    render(<ModelChip session="s1" hasMessages />);
    fireEvent.click(await screen.findByRole("button", { name: /Opus 5\.5/ }));
    fireEvent.click(screen.getAllByRole("menuitemradio")[2]);
    expect(posts).toEqual([]);
    expect(screen.getByText(/resets this chat's cache/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Switch to Sonnet 5/ }));
    await waitFor(() => expect(posts.find((p) => p.url === "/api/chat/level")?.body).toMatchObject({ level: 3, switch: true }));
  });
});

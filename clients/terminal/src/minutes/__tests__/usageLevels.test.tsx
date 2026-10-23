/** The usage meter, the chat's limit card and the level picker (features `usage` and `levels`,
 *  PLAN-8H 6.2/6.3; the approved mock is proto-on-product desktop/proto/Usage.tsx + Level.tsx). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { setFeatureOverride } from "../../platform/features";
import { UsageLimitCard, UsageMeter, isUsageLimit } from "../../surfaces/usage/Usage";
import { LevelPicker } from "../../surfaces/usage/Level";
import { FaultBlock } from "../../surfaces/agent-window";
import { LEVEL_SET_EVENT } from "../../platform/events";

const NOW = Date.UTC(2026, 9, 16, 12, 0) / 1000;
const usage = (five: number, week: number, extra = false) => ({
  five_h: { pct: five, used: five / 100, allowance: 1, resets_at: NOW + 3600 },
  week: { pct: week, used: week / 10, allowance: 10, resets_at: NOW + 86400 }, extra, markup: 1.3,
});

type Call = { url: string; init?: RequestInit };
function server(routes: Record<string, (body: unknown) => unknown>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = `${init?.method ?? "GET"} ${url.split("?")[0]}`;
    const h = routes[key];
    if (!h) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify(h(init?.body ? JSON.parse(String(init.body)) : null)), { status: 200 });
  }));
  return calls;
}

beforeEach(() => setFeatureOverride("all"));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setFeatureOverride("all"); });

describe("UsageMeter", () => {
  it("shows both windows as percentages beside the account, and a popover with the limit", async () => {
    server({ "GET /api/usage": () => usage(42, 18) });
    render(<UsageMeter />);
    const chip = await screen.findByRole("button", { name: /42% · 5h · 18% · week/ });
    fireEvent.click(chip);
    expect(screen.getByRole("dialog", { name: "Usage" })).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "5-hour window" }).getAttribute("aria-valuenow")).toBe("42");
    expect(screen.queryByText(/You've reached/)).toBeNull();
  });

  it("at a full window says when it is back and turns on extra usage (recorded only)", async () => {
    const calls = server({ "GET /api/usage": () => usage(100, 30), "POST /api/usage": () => usage(100, 30, true) });
    render(<UsageMeter />);
    fireEvent.click(await screen.findByRole("button", { name: /100% · 5h/ }));
    expect(screen.getByText(/You've reached this 5-hour limit — back at/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "+ extra usage" })); });
    const post = calls.find((c) => c.init?.method === "POST");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ extra: true });
    expect(await screen.findByRole("button", { name: "Extra usage on" })).toBeTruthy();
  });

  it("is not drawn and reads nothing with `usage` off", async () => {
    const calls = server({ "GET /api/usage": () => usage(42, 18) });
    setFeatureOverride({ usage: false });
    render(<UsageMeter />);
    await act(async () => {});
    expect(screen.queryByRole("button", { name: /5h/ })).toBeNull();
    expect(calls.filter((c) => c.url.startsWith("/api/usage"))).toEqual([]);
  });
});

describe("the limit card", () => {
  const detail = "You've reached this 5-hour limit — back at 17:00 UTC. Your message is kept; turn on extra usage to carry on now.";
  const fault = { source: "model-provider", kind: "unpaid", status: 402, detail, remedy: "Wait for the window to reset, or turn on extra usage." };

  it("knows the refusal by its sentence, and nothing else", () => {
    expect(isUsageLimit(fault)).toBe(true);
    expect(isUsageLimit({ ...fault, detail: "You have reached your AI usage limit for today." })).toBe(false);
  });

  it("is what the chat draws for a full window: back at, + extra usage, Wait, then Send again", async () => {
    server({ "POST /api/usage": () => usage(100, 30, true) });
    const retry = vi.fn();
    render(<FaultBlock failed={{ fault, retry: true }} onRetry={retry} />);
    expect(screen.getByText("You've reached this 5-hour limit — back at 17:00 UTC")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Wait" })).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "+ extra usage" })); });
    fireEvent.click(await screen.findByRole("button", { name: "Send again" }));
    expect(retry).toHaveBeenCalled();
  });

  it("Wait keeps the message and offers nothing else", () => {
    render(<UsageLimitCard detail={detail} />);
    fireEvent.click(screen.getByRole("button", { name: "Wait" }));
    expect(screen.queryByRole("button", { name: "+ extra usage" })).toBeNull();
  });
});

describe("LevelPicker", () => {
  const levels = (chat: { level: number; model: string } | null) => ({
    levels: [1, 2, 3, 4, 5].map((n) => ({ level: n, word: "", model: `m${n}` })), chat });

  it("a new chat picks 1–5 at its start", async () => {
    const calls = server({ "GET /api/models/levels": () => levels(null), "POST /api/chat/level": () => ({ ok: true }) });
    render(<LevelPicker session="s1" hasMessages={false} />);
    const group = await screen.findByRole("radiogroup", { name: "Model level" });
    expect(group.textContent).toContain("balanced");                     // level 3 until picked
    await act(async () => { fireEvent.click(screen.getByRole("radio", { name: "5" })); });
    const post = calls.find((c) => c.init?.method === "POST");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ session: "s1", level: 5, switch: true });
    expect(screen.getByRole("radio", { name: "5" }).getAttribute("aria-checked")).toBe("true");
  });

  it("after the first message it is locked: a badge, and changing it is an explicit switch", async () => {
    const calls = server({ "GET /api/models/levels": () => levels({ level: 2, model: "m2" }), "POST /api/chat/level": () => ({ ok: true }) });
    const onNew = vi.fn();
    render(<LevelPicker session="s1" hasMessages onNewChat={onNew} />);
    fireEvent.click(await screen.findByRole("button", { name: "Level 2" }));
    fireEvent.click(screen.getByRole("radio", { name: "4" }));
    fireEvent.click(screen.getByRole("button", { name: "Start a new chat at level 4" }));
    expect(onNew).toHaveBeenCalledWith(4);
    fireEvent.click(screen.getByRole("button", { name: "Level 2" }));
    fireEvent.click(screen.getByRole("radio", { name: "4" }));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Switch (resets the cache)" })); });
    expect(JSON.parse(String(calls.find((c) => c.init?.method === "POST")?.init?.body))).toEqual({ session: "s1", level: 4, switch: true });
  });

  it("draws nothing when the deployment offers no levels", async () => {
    server({ "GET /api/models/levels": () => ({ levels: [1, 2, 3, 4, 5].map((n) => ({ level: n, word: "", model: null })) }) });
    const { container } = render(<LevelPicker session="s1" hasMessages={false} />);
    await act(async () => {});
    expect(container.textContent).toBe("");
  });
});

describe("an answer of another shape", () => {
  it("draws nothing rather than failing (an older server, a proxy page)", async () => {
    server({ "GET /api/usage": () => ({ detail: "nope" }), "GET /api/models/levels": () => ({ models: [] }) });
    const { container } = render(<><UsageMeter /><LevelPicker session="s1" hasMessages={false} /></>);
    await act(async () => {});
    expect(container.textContent).toBe("");
  });
});


describe("a new chat at a level", () => {
  it("the picker of the new chat shows the level the shell set on it (LEVEL_SET_EVENT)", async () => {
    let chat: { level: number; model: string } | null = null;
    server({ "GET /api/models/levels": () => ({ levels: [1, 2, 3, 4, 5].map((n) => ({ level: n, word: "", model: `m${n}` })), chat }) });
    render(<LevelPicker session="new1" hasMessages={false} />);
    expect((await screen.findByRole("radio", { name: "3" })).getAttribute("aria-checked")).toBe("true");
    chat = { level: 4, model: "m4" };
    await act(async () => { window.dispatchEvent(new CustomEvent(LEVEL_SET_EVENT, { detail: { session: "new1", level: 4 } })); });
    expect(screen.getByRole("radio", { name: "4" }).getAttribute("aria-checked")).toBe("true");
  });
});

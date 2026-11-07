/** A PERSON'S DEFAULT TARGET — the workspace their new chats write to (founder 2026-10-13).
 *
 *  The "Saves to …" menu gains "Make default for new chats" and shows which one is the default. The
 *  default lives on the server (`/api/chat/default-target`, per person), which re-checks it on every
 *  read and answers null — Personal, unnamed — for a workspace the person can no longer write. A new
 *  chat starts from that answer; an existing chat keeps its own target. The server half is pinned in
 *  `core/agent/tests/test_chat_default_target.py`.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, fireEvent, act, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { ContextBar } from "../ContextBar";
import { newChat, withDefaultTarget } from "../chats";
import { getDefaultTarget, setDefaultTarget } from "../../data/workspaceApi";

vi.mock("../../ui-kit/WsLink", () => ({
  WorkspaceName: ({ slug }: { slug: string }) => <span>{slug === "biz-1a2b3c" ? "Biz" : slug}</span>,
}));
vi.mock("../../ui-kit/wsLinks", () => ({
  workspaceBySlug: async (slug: string) => ({ id: "aaaaaaaaaa", name: slug, kind: "group", slug,
    access: "readable", writable: true }),
}));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

const BIZ = "biz-1a2b3c";
const sel = (target?: string) => ({
  kind: "chat" as const, chatId: "c1", label: "A chat", workspaces: ["personal", "_global", BIZ], target,
});

async function bar(target: string | undefined, defaultTarget: string, onMakeDefault = vi.fn()) {
  const out = render(
    <ContextBar sel={sel(target)} flavor="chat" memberships={[]} onAddWorkspace={vi.fn()}
      onRemoveWorkspace={vi.fn()} onSetTarget={vi.fn()} defaultTarget={defaultTarget} onMakeDefault={onMakeDefault} />);
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Workspaces for this chat" }));
  return { ...out, onMakeDefault };
}

// ── the menu ─────────────────────────────────────────────────────────────────────────────────────

describe("the Saves to menu", () => {
  it("offers to make the chat's target the default for new chats", async () => {
    const { onMakeDefault } = await bar(BIZ, "");
    const item = document.querySelector('[data-ctx="make-default"]') as HTMLElement;
    expect(item.textContent).toMatch(/Make default for new chats/);
    fireEvent.click(item);
    expect(onMakeDefault).toHaveBeenCalledWith(BIZ);
  });

  it("says so when the chat already writes to the default", async () => {
    await bar(BIZ, BIZ);
    const item = document.querySelector('[data-ctx="make-default"]') as HTMLElement;
    expect(item.textContent).toMatch(/Default for new chats/);
    expect(item.getAttribute("aria-disabled")).toBe("true");
  });

  it("Personal can be the default again", async () => {
    const { onMakeDefault } = await bar(undefined, BIZ);
    fireEvent.click(document.querySelector('[data-ctx="make-default"]') as HTMLElement);
    expect(onMakeDefault).toHaveBeenCalledWith("");
  });

  it("is absent where nobody wired it — the admin account has one workspace and no menu", async () => {
    render(<ContextBar sel={sel(BIZ)} flavor="chat" memberships={[]} onAddWorkspace={vi.fn()}
      onRemoveWorkspace={vi.fn()} />);
    await settle();
    expect(document.querySelector('[data-ctx="make-default"]')).toBeNull();
  });
});

// ── a new chat ───────────────────────────────────────────────────────────────────────────────────

describe("a new chat starts on the default", () => {
  it("mounts the default and writes there", () => {
    const d = withDefaultTarget(newChat("New chat", ["personal", "_global"], { touched: false }), BIZ);
    expect(d.workspaces).toEqual(["personal", "_global", BIZ]);
    expect(d.target).toBe(BIZ);
  });

  it("Personal (no default) leaves the chat as it was", () => {
    const c = newChat("New chat", ["personal", "_global"], { touched: false });
    expect(withDefaultTarget(c, "")).toEqual(c);
    expect(withDefaultTarget(c, "personal")).toEqual(c);
  });

  it("never moves a chat that already chose where it writes", () => {
    const c = { ...newChat("Old", ["personal", "_global", "other-9z"], { touched: true }), target: "other-9z" };
    expect(withDefaultTarget(c, BIZ)).toEqual(c);
  });
});

describe("the default on the wire", () => {
  function stub(answer: () => Response) {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: (init?.method || "GET").toUpperCase(), body: init?.body ? JSON.parse(String(init.body)) : undefined });
      return answer();
    }));
    return calls;
  }

  it("reads the server's re-checked answer", async () => {
    stub(() => new Response(JSON.stringify({ target: BIZ }), { status: 200 }));
    expect(await getDefaultTarget()).toBe(BIZ);
  });

  it("a workspace the person can no longer write comes back as Personal", async () => {
    stub(() => new Response(JSON.stringify({ target: null }), { status: 200 }));
    expect(await getDefaultTarget()).toBe("");
  });

  it("a server that cannot answer is Personal, never a guess", async () => {
    stub(() => new Response("{}", { status: 502 }));
    expect(await getDefaultTarget()).toBe("");
  });

  it("stores it with PUT, and the server's refusal reaches the caller", async () => {
    const calls = stub(() => new Response(JSON.stringify({ target: BIZ }), { status: 200 }));
    expect(await setDefaultTarget(BIZ)).toBe(BIZ);
    expect(calls[0]).toEqual({ url: "/api/chat/default-target", method: "PUT", body: { workspace: BIZ } });
    stub(() => new Response(JSON.stringify({ detail: "not a workspace you can write" }), { status: 403 }));
    await expect(setDefaultTarget("x-1")).rejects.toBeTruthy();
  });
});

describe("the shell", () => {
  const shell = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");
  it("asks the server for the default for each new draft, and applies it through the one target writer", () => {
    expect(shell).toMatch(/getDefaultTarget\(\)/);
    expect(shell).toMatch(/setChatTargetRef\.current\(wid, \{ justMounted: true \}\)/);
    expect(shell).toMatch(/onMakeDefault=/);
  });
});

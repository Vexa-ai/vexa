/** @ IN A PRIVATE CHAT (founder validation O1, 2026-10-18: he typed `call @megan` in a private chat
 *  and the picker offered nobody — it showed only in rooms). With rooms on, a private chat now offers
 *  the people who share a workspace with you (the server's per-viewer list), a pick tags them for the
 *  agent, and its choice is Tag or "Call <name>" — never "Invite to this chat", because a private chat
 *  is never shared from here. "Call Anna" asks the shell to ring her in the room of two. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";

const stream = vi.hoisted(() => ({ calls: [] as { req: { prompt: string; mentions?: string[] } }[] }));
vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: (req: unknown) => {
    stream.calls.push({ req: req as never });
    return Promise.resolve({ sawVisibleOutput: true, terminal: true, aborted: false, cursor: "1-0" });
  },
}));

import { Chat } from "../chat";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../platform/layout";
import { setFeatureOverride } from "../../platform/features";
import { CALL_PERSON_EVENT } from "../../platform/events";

const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);
let seq = 0;
const posts: string[] = [];
const known: string[] = [];

beforeEach(() => {
  stream.calls.length = 0; posts.length = 0; known.length = 0;
  setFeatureOverride({ rooms: true, shared_chats: true });
  globalThis.fetch = vi.fn(async (url: unknown, init?: RequestInit) => {
    const u = String(url);
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });
    if (init?.method === "POST") posts.push(u);
    if (u.startsWith("/api/people/mentionable")) return ok({ people: [{ subject: "u_anna", name: "Anna Berg", workspaces: ["team"] }], agents: [{ name: "vexa" }] });
    if (u.startsWith("/api/people/known")) { known.push(u); return ok({ people: [{ key: "x", name: "Andy External", vexa: false }], count: 1 }); }
    if (/\/api\/sessions\/[^/]+\/room/.test(u)) return ok({ session_id: "s-1", room: false, humans: 1, visibility: "private", proactivity: "off", workspace: "" });
    if (u.startsWith("/api/chat/pending")) return ok({ pending: [], cursor: "9-0" });
    return ok({ turns: [], sessions: [] });
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); setFeatureOverride("all"); });

async function typeAt() {
  seq += 1;
  render(<ServicesProvider container={container()}><Chat params={{ session: `p-${seq}` }} /></ServicesProvider>);
  const box = screen.getByPlaceholderText(/ask the agent/) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: "call @an", selectionStart: "call @an".length } });
  await waitFor(() => expect(screen.getByRole("listbox", { name: "People you can mention" })).toBeTruthy());
  return box;
}

describe("@ in a private chat", () => {
  it("offers the people who share a workspace with you, from the server's per-viewer list", async () => {
    await typeAt();
    const options = screen.getAllByRole("option").map((o) => o.textContent ?? "");
    expect(options.some((t) => t.includes("@Anna"))).toBe(true);
    expect(options.some((t) => t.includes("Andy"))).toBe(false);   // not someone you share a workspace with
  });

  it("a pick is a tag or a call — never an invite into this private chat", async () => {
    const box = await typeAt();
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(box.value).toBe("call @Anna "));
    expect(await screen.findByText("Call Anna")).toBeTruthy();
    expect(screen.queryByText("Invite to this chat")).toBeNull();
  });

  it("Call Anna asks the shell to ring her in the room of two; nothing is shared or sent", async () => {
    const box = await typeAt();
    fireEvent.keyDown(box, { key: "Enter" });
    const asked: unknown[] = [];
    const on = (e: Event) => asked.push((e as CustomEvent).detail);
    window.addEventListener(CALL_PERSON_EVENT, on);
    fireEvent.click(await screen.findByText("Call Anna"));
    window.removeEventListener(CALL_PERSON_EVENT, on);
    expect(asked).toEqual([{ subject: "u_anna", name: "Anna Berg" }]);
    expect(posts.filter((p) => p.includes("/participants"))).toEqual([]);
    expect(stream.calls.length).toBe(0);
  });

  it("a tag rides to the agent as a mention, and the chat is not shared", async () => {
    const box = await typeAt();
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(await screen.findByText("Tag"));
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(stream.calls.length).toBe(1));
    expect(stream.calls[0].req.mentions).toEqual(["u_anna"]);
    expect(posts.filter((p) => p.includes("/participants") || p.includes("/share"))).toEqual([]);
  });
});

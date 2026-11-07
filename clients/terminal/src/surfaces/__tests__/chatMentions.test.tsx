/** ADR-0048 — @-mentions in the mounted chat: the picker shows only who the server lists, Enter on a
 *  message that mentions someone not in the chat asks once before anything is sent, and each answer
 *  does what it says — share and send (participants API, by account id), send without sharing (the
 *  name as plain text, no mention ids), cancel (nothing). The whole `Chat` is mounted, as
 *  `budgetStop.test.tsx` does, because the defect class here is wiring. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";

const stream = vi.hoisted(() => ({ calls: [] as { req: { prompt: string; session: string; mentions?: string[]; to_agent?: boolean } }[] }));
vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: (req: unknown) => {
    stream.calls.push({ req: req as never });
    return Promise.resolve({ sawVisibleOutput: true, terminal: true, aborted: false, cursor: "1-0" });
  },
}));

import { Chat } from "../chat";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService, OPEN_SESSION_EVENT } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../platform/layout";
import { setFeatureOverride } from "../../platform/features";

const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);

let shared = false;
const posts: { url: string; body: unknown }[] = [];
let seq = 0;
function mountChat() {
  seq += 1;
  render(<ServicesProvider container={container()}><Chat params={{ session: `m-${seq}` }} /></ServicesProvider>);
  return `m-${seq}`;
}

beforeEach(() => {
  stream.calls.length = 0; posts.length = 0; shared = false;
  try { localStorage.clear(); } catch { /* jsdom */ }
  globalThis.fetch = vi.fn(async (url: unknown, init?: RequestInit) => {
    const u = String(url);
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });
    if (u.startsWith("/api/people/mentionable")) {
      return ok({ people: [{ subject: "u_anna", name: "Anna Berg", email: "anna@acme.test" }], agents: [{ name: "vexa" }] });
    }
    if (u.includes("/share-preview")) return ok({ session_id: "s-p", forks: !shared, routine: null, private_reads: [], participants: shared ? 2 : 1 });
    if (u.includes("/participants") && init?.method === "POST") {
      posts.push({ url: u, body: JSON.parse(String(init.body)) });
      return ok({ session_id: shared ? "s-p" : "s-new", participants: [] });
    }
    if (u.includes("/participants")) {
      return ok({ session_id: "s-p", you: "me", role: "owner", participants: shared
        ? [{ principal: "me", kind: "human", role: "owner" }, { principal: "u_bo", kind: "human", role: "member" }]
        : [{ principal: "me", kind: "human", role: "owner" }] });
    }
    if (u.startsWith("/api/chat/pending")) return ok({ pending: [], cursor: "9-0" });
    return ok({ turns: [], sessions: [] });
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); setFeatureOverride("all"); });

async function mentionAnna(rest = "share acme with ") {
  const box = screen.getByPlaceholderText(/ask the agent/) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: `${rest}@an`, selectionStart: `${rest}@an`.length } });
  await waitFor(() => expect(screen.getByRole("listbox", { name: "People you can mention" })).toBeTruthy());
  return box;
}

describe("chat @-mentions", () => {
  it("the picker shows only the people the server lists", async () => {
    mountChat();
    await mentionAnna();
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options.some((t) => t?.includes("@Anna"))).toBe(true);
    expect(options.length).toBe(1);               // "an" does not match @vexa; nobody else exists
  });

  it("Enter asks before sending; Send without sharing sends the name as plain text", async () => {
    mountChat();
    const box = await mentionAnna();
    fireEvent.keyDown(box, { key: "Enter" });     // picks Anna
    await waitFor(() => expect(box.value).toBe("share acme with @Anna "));
    fireEvent.click(await screen.findByText("Invite to this chat"));   // a tag unless chosen (founder 2026-10-15)
    fireEvent.keyDown(box, { key: "Enter" });     // sends → asks
    await waitFor(() => expect(screen.getByRole("dialog", { name: "Share this chat with Anna Berg?" })).toBeTruthy());
    expect(stream.calls.length).toBe(0);
    expect(screen.getByText(/joins this chat and sees what was said here/)).toBeTruthy();
    fireEvent.click(screen.getByText("Send without sharing"));
    await waitFor(() => expect(stream.calls.length).toBe(1));
    expect(stream.calls[0].req.mentions).toBeUndefined();
    expect(stream.calls[0].req.prompt).toContain("@Anna");
    expect(posts).toEqual([]);
  });

  it("Cancel sends nothing and shares nothing", async () => {
    mountChat();
    const box = await mentionAnna();
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(await screen.findByText("Invite to this chat"));
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByText("Cancel")).toBeTruthy());
    fireEvent.click(screen.getByText("Cancel"));
    expect(stream.calls.length).toBe(0);
    expect(posts).toEqual([]);
  });

  it("Share and send in a chat already shared adds by account id, then sends with the mention", async () => {
    shared = true;
    mountChat();
    const box = await mentionAnna();
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(await screen.findByText("Invite to this chat"));
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByText("Share and send")).toBeTruthy());
    fireEvent.click(screen.getByText("Share and send"));
    await waitFor(() => expect(stream.calls.length).toBe(1));
    expect(posts.map((p) => p.body)).toEqual([{ subject: "u_anna", role: "member" }]);
    expect(stream.calls[0].req.mentions).toEqual(["u_anna"]);
  });

  it("Share and send in a private chat shares it in place, history included, and sends here", async () => {
    mountChat();
    const box = await mentionAnna();
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(await screen.findByText("Invite to this chat"));
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByText("Share and send")).toBeTruthy());
    fireEvent.click(screen.getByText("Share and send"));
    await waitFor(() => expect(stream.calls.length).toBe(1));
    expect(posts.map((p) => p.body)).toEqual([{ subject: "u_anna", history: "all", role: "member" }]);
  });

  it("New chat with them opens a new shared chat for the message", async () => {
    const opened: string[] = [];
    const onOpen = (e: Event) => opened.push((e as CustomEvent<{ session: string }>).detail.session);
    window.addEventListener(OPEN_SESSION_EVENT, onOpen);
    mountChat();
    const box = await mentionAnna();
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(await screen.findByText("Invite to this chat"));
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByText("New chat with them")).toBeTruthy());
    fireEvent.click(screen.getByText("New chat with them"));
    await waitFor(() => expect(opened).toEqual(["s-new"]));
    expect(posts.map((p) => p.body)).toEqual([{ subject: "u_anna", history: "none", role: "member" }]);
    expect(stream.calls.length).toBe(0);          // not sent into the private chat
    window.removeEventListener(OPEN_SESSION_EVENT, onOpen);
  });

  it("a pick is a tag by default: the message goes without asking, nothing is shared (founder: \"@ is to tag and to invite\")", async () => {
    mountChat();
    const box = await mentionAnna();
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByText("Tag")).toBeTruthy());
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(stream.calls.length).toBe(1));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posts).toEqual([]);
  });

  it("invite to a call shares first, with the confirm, and nothing is sent before it is answered", async () => {
    const calls: string[] = [];
    const onCall = (e: Event) => calls.push((e as CustomEvent<{ session: string }>).detail.session);
    window.addEventListener("vexa:terminal:call-start", onCall);
    mountChat();
    const box = await mentionAnna();
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(await screen.findByText("Invite to a call"));
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByText("Cancel")).toBeTruthy());
    expect(stream.calls.length).toBe(0);
    expect(calls).toEqual([]);                    // no ring before the person confirms
    fireEvent.click(screen.getByText("Cancel"));
    expect(posts).toEqual([]);
    window.removeEventListener("vexa:terminal:call-start", onCall);
  });

  it("with shared_chats switched off (#1783) the @ picker offers nobody and the people list is not read", async () => {
    setFeatureOverride({ shared_chats: false });
    mountChat();
    const box = screen.getByPlaceholderText(/ask the agent/) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "hi @an", selectionStart: 6 } });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("listbox", { name: "People you can mention" })).toBeNull();
    const urls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.startsWith("/api/people/mentionable"))).toBe(false);
  });
});

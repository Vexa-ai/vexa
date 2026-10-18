/** UI-DECISIONS 7g: one inline card for questionnaires and approvals; a click is the person's own
 *  message, bound to the card's PENDING ITEM (0.13.4 S5: open → nonce + hash → answer → agent-api's
 *  message is sent). An agent writes it as an ```ask fence in its reply. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ASK_CHAT_EVENT } from "../../platform/events";
import { InlineCard, parseInlineCard } from "../InlineCard";
import { MdxDoc } from "../MdxDoc";

const asks: { prompt: string; display?: string }[] = [];
window.addEventListener(ASK_CHAT_EVENT, (e) => asks.push((e as CustomEvent).detail));
afterEach(() => { cleanup(); asks.length = 0; vi.unstubAllGlobals(); });

/** agent-api's ask routes, answering what a bound press needs */
function askApi() {
  const calls: { url: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (u: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    calls.push({ url: String(u), body });
    if (String(u).endsWith("/api/asks/open")) return new Response(JSON.stringify({ pending_id: "p-1", nonce: "n-1", content_hash: "h-1", status: "open" }), { status: 200 });
    const said = body.reply?.reply ?? body.reply?.label;
    return new Response(JSON.stringify({ status: "answered", message: `[answer ws-who] ${said}` }), { status: 200 });
  }));
  return calls;
}

const Q = { id: "ws-who", question: "Who's it for?", chips: ["Just me", { label: "Me and others", reply: "shared" }], free: "Or describe it" };

describe("parseInlineCard", () => {
  it("reads a card and refuses what is not one", () => {
    expect(parseInlineCard(JSON.stringify(Q))?.chips?.map((c) => c.label)).toEqual(["Just me", "Me and others"]);
    expect(parseInlineCard('{"id":"x"}')).toBeNull();
    expect(parseInlineCard('{"id":"bad id!","question":"q"}')).toBeNull();
    expect(parseInlineCard("nope")).toBeNull();
  });
});

describe("InlineCard", () => {
  it("a chip is the person's answer, bound to the card's pending item, once", async () => {
    const calls = askApi();
    render(<InlineCard spec={parseInlineCard(Q)!} session="s-1" />);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/api/asks/open"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Me and others" }));
    await waitFor(() => expect(asks).toEqual([{ prompt: "[answer ws-who] shared", display: "Me and others" }]));
    expect(calls.find((c) => c.url.endsWith("/api/asks/p-1/answer"))?.body).toEqual({ nonce: "n-1", content_hash: "h-1", reply: { label: "Me and others", reply: "shared" } });
    expect(screen.queryByRole("button", { name: "Just me" })).toBeNull();
    expect(screen.getByText("Who's it for? → Me and others")).toBeTruthy();
  });
  it("free text answers too", async () => {
    askApi();
    render(<InlineCard spec={parseInlineCard(Q)!} session="s-1" />);
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "the Lisbon meetup" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(asks.at(-1)).toEqual({ prompt: "[answer ws-who] the Lisbon meetup", display: "the Lisbon meetup" }));
  });
  it("an ```ask fence in a reply draws the card", async () => {
    render(<MdxDoc>{"Let's set up a workspace.\n\n```ask\n" + JSON.stringify(Q) + "\n```"}</MdxDoc>);
    await waitFor(() => expect(document.querySelector("[data-inline-card=ws-who]")).toBeTruthy());
  });
});

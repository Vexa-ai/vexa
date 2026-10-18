/** UI-DECISIONS 7g: one inline card for questionnaires and approvals; a click is the person's own
 *  message bound to the card's id. An agent writes it as an ```ask fence in its reply. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ASK_CHAT_EVENT } from "../../platform/events";
import { InlineCard, parseInlineCard } from "../InlineCard";
import { MdxDoc } from "../MdxDoc";

const asks: { prompt: string; display?: string }[] = [];
window.addEventListener(ASK_CHAT_EVENT, (e) => asks.push((e as CustomEvent).detail));
afterEach(() => { cleanup(); asks.length = 0; });

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
  it("a chip is the person's answer, bound to the card, once", () => {
    render(<InlineCard spec={parseInlineCard(Q)!} />);
    fireEvent.click(screen.getByRole("button", { name: "Me and others" }));
    expect(asks).toEqual([{ prompt: "[answer ws-who] shared", display: "Me and others" }]);
    expect(screen.queryByRole("button", { name: "Just me" })).toBeNull();
    expect(screen.getByText("Who's it for? → Me and others")).toBeTruthy();
  });
  it("free text answers too", () => {
    render(<InlineCard spec={parseInlineCard(Q)!} />);
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "the Lisbon meetup" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(asks.at(-1)).toEqual({ prompt: "[answer ws-who] the Lisbon meetup", display: "the Lisbon meetup" });
  });
  it("an ```ask fence in a reply draws the card", async () => {
    render(<MdxDoc>{"Let's set up a workspace.\n\n```ask\n" + JSON.stringify(Q) + "\n```"}</MdxDoc>);
    await waitFor(() => expect(document.querySelector("[data-inline-card=ws-who]")).toBeTruthy());
  });
});

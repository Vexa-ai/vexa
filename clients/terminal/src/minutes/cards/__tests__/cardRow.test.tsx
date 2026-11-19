/** 7m + R11: a card row opens on a tap and never decides; it says who the card waits on, or how it
 *  ended; Decline lives in "⋯" (last) and is held with Undo. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { resetPeople } from "../../desktop/cardsApi";
import { OPEN_CARD_SHEET_EVENT, resetInbox } from "../api";
import { CardRow } from "../CardRow";
import { DECLINE_HOLD_MS } from "../hooks";
import { card, flush, NOW, PEOPLE, routeFetch } from "./fixtures";

const opened: string[] = [];
const onOpen = (e: Event) => opened.push((e as CustomEvent<{ id: string }>).detail.id);
beforeEach(() => { window.addEventListener(OPEN_CARD_SHEET_EVENT, onOpen); vi.spyOn(Date, "now").mockReturnValue(NOW); });
afterEach(() => { cleanup(); window.removeEventListener(OPEN_CARD_SHEET_EVENT, onOpen); opened.length = 0; resetPeople(); resetInbox(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("CardRow", () => {
  it("a tap on the row or its title OPENS the card and never decides (R11)", async () => {
    const f = routeFetch({ ...PEOPLE });
    render(<CardRow card={card()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open the card: Send the agenda" }));
    fireEvent.click(screen.getByText("Send the agenda"));
    await flush();
    expect(opened).toEqual(["c-1", "c-1"]);
    expect(f.to("/card/decide")).toHaveLength(0);
    expect(f.to("/card/nonce")).toHaveLength(0);
  });

  it("shows ONE outlined primary action in its fixed place; Decline is not on the row", () => {
    routeFetch({ ...PEOPLE });
    const { container } = render(<CardRow card={card()} />);
    const acts = container.querySelectorAll(".vx-crow-act .vx-btn");
    expect(acts).toHaveLength(1);
    expect(acts[0].textContent).toBe("Accept");
    expect(acts[0].hasAttribute("data-primary")).toBe(true);
    expect(acts[0].getAttribute("data-variant")).toBe("secondary");   // outlined, never a solid fill (7r)
    expect(screen.queryByRole("button", { name: "Decline" })).toBeNull();
  });

  it("a viewer who is not the next actor reads who it waits on and for how long", async () => {
    routeFetch({ ...PEOPLE });
    render(<CardRow card={card({ next_actor: "p-ana", viewer: { role: "requester", seen: true, actions: [] } })} />);
    await waitFor(() => expect(screen.getByText(/Waiting on Ana · 8 h/)).toBeTruthy());
    expect(screen.getByRole("button", { name: "Open" })).toBeTruthy();
  });

  it("a decided card folds to who decided it", async () => {
    routeFetch({ ...PEOPLE });
    const at = new Date(NOW - 2 * 86400_000).toISOString();
    render(<CardRow card={card({ open: false, viewer: { role: "requester", seen: true, actions: [] }, last_decision: { action: "acknowledge", by: "p-val", at } })} />);
    await waitFor(() => expect(screen.getByText(/^Accepted by Valeriia · /)).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  });

  it("an unknown person is 'Someone', never an id", async () => {
    routeFetch({ ...PEOPLE });
    render(<CardRow card={card({ next_actor: "p-unknown", viewer: { role: "watcher", seen: true, actions: [] } })} />);
    await waitFor(() => expect(screen.getByText(/Waiting on Someone/)).toBeTruthy());
    expect(document.body.textContent).not.toContain("p-unknown");
  });

  it("Decline is the last item in ⋯, is held with Undo, and Undo sends nothing", async () => {
    const f = routeFetch({ ...PEOPLE, "/card/nonce": () => ({ body: { nonce: "n1", content_hash: "h1", revision: 3 } }), "/card/decide": () => ({ body: { status: "saved" } }) });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    render(<CardRow card={card()} />);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const items = screen.getAllByRole("menuitem");
    expect(items.at(-1)!.textContent).toBe("Decline");
    fireEvent.click(items.at(-1)!);
    const undo = screen.getByRole("button", { name: "Undo" });
    fireEvent.click(undo);
    await vi.advanceTimersByTimeAsync(DECLINE_HOLD_MS + 100);
    await flush();
    expect(f.to("/card/decide")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Accept" })).toBeTruthy();
  });
});

/** D14 / P21: `seen` is written only when the card is really in view (≥50% for ≥1 s, document
 *  visible), once per card per mount — never on fetch or hover. And the one Inbox store: the Now
 *  strip, the Inbox header and a room's badge show the same count from one answer. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { resetPeople } from "../../desktop/cardsApi";
import { NowStrip } from "../../desktop/home2/NowStrip";
import { resetInbox } from "../api";
import { CardRow } from "../CardRow";
import { SEEN_DWELL_MS } from "../hooks";
import { InboxCards, RoomBadge } from "../InboxCards";
import { card, flush, PEOPLE, routeFetch } from "./fixtures";

type IOCallback = (entries: { isIntersecting: boolean; intersectionRatio: number }[]) => void;
let observers: { cb: IOCallback; el: Element | null }[] = [];
class IO {
  cb: IOCallback; el: Element | null = null;
  constructor(cb: IOCallback) { this.cb = cb; observers.push(this); }
  observe(el: Element) { this.el = el; }
  disconnect() { observers = observers.filter((o) => o !== this); }
  unobserve() {}
  takeRecords() { return []; }
}
const show = (ratio: number) => observers.forEach((o) => o.cb([{ isIntersecting: ratio > 0, intersectionRatio: ratio }]));

beforeEach(() => { vi.stubGlobal("IntersectionObserver", IO); });
afterEach(() => { cleanup(); observers = []; resetPeople(); resetInbox(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("seen only on in-view (D14)", () => {
  it("is not written on render, fetch or hover — only after 1 s at ≥50% visible", async () => {
    const f = routeFetch({ ...PEOPLE, "/card/seen": () => ({ body: { seen_at: "2026-10-17T12:00:00Z" } }) });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    render(<CardRow card={card()} />);
    fireEvent.mouseOver(screen.getByText("Send the agenda"));
    await vi.advanceTimersByTimeAsync(SEEN_DWELL_MS * 3);
    expect(f.to("/card/seen")).toHaveLength(0);
    show(0.3);                                         // under half: not seen
    await vi.advanceTimersByTimeAsync(SEEN_DWELL_MS * 2);
    expect(f.to("/card/seen")).toHaveLength(0);
    show(0.6);
    await vi.advanceTimersByTimeAsync(SEEN_DWELL_MS - 100);
    show(0);                                           // scrolled away before a second: not seen
    await vi.advanceTimersByTimeAsync(SEEN_DWELL_MS);
    expect(f.to("/card/seen")).toHaveLength(0);
    show(0.6);
    await vi.advanceTimersByTimeAsync(SEEN_DWELL_MS + 10);
    await flush();
    expect(f.to("/card/seen").map((c) => c.body)).toEqual([{ card: "c-1" }]);
    show(0); show(1);                                  // once per card per mount
    await vi.advanceTimersByTimeAsync(SEEN_DWELL_MS * 2);
    expect(f.to("/card/seen")).toHaveLength(1);
  });

  it("is not written while the document is hidden", async () => {
    const f = routeFetch({ ...PEOPLE, "/card/seen": () => ({ body: {} }) });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const vis = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    render(<CardRow card={card()} />);
    show(1);
    await vi.advanceTimersByTimeAsync(SEEN_DWELL_MS * 3);
    expect(f.to("/card/seen")).toHaveLength(0);
    vis.mockReturnValue("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(SEEN_DWELL_MS + 10);
    await flush();
    expect(f.to("/card/seen")).toHaveLength(1);
  });

  it("a card already seen is not written again", async () => {
    const f = routeFetch({ ...PEOPLE, "/card/seen": () => ({ body: {} }) });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    render(<CardRow card={card({ viewer: { role: "next_actor", seen: true, actions: ["acknowledge"] } })} />);
    show(1);
    await vi.advanceTimersByTimeAsync(SEEN_DWELL_MS * 3);
    expect(f.to("/card/seen")).toHaveLength(0);
  });
});

describe("one Inbox store, one count", () => {
  it("the Now strip, the Inbox header and the room badge agree", async () => {
    const mine = (id: string) => card({ id, title: `Card ${id}`, workspaces: ["growth"] });
    const items = [mine("a"), mine("b"), mine("c"), card({ id: "d", title: "Theirs", next_actor: "p-ana", viewer: { role: "requester", seen: true, actions: [] } })];
    const f = routeFetch({ ...PEOPLE, "/card/inbox": () => ({ body: { items, counts: { waiting_on_me: 3, due_soon: 0, i_wait_on: 1 } } }) });
    render(<><NowStrip workspaces={[]} /><InboxCards /><RoomBadge workspace="growth" /></>);
    await waitFor(() => expect(document.querySelector("[data-now-cards] [data-count]")).toBeTruthy());
    const now = document.querySelector("[data-now-cards] [data-count]")!.getAttribute("data-count");
    const inbox = document.querySelector(".vx-cin-count")!.getAttribute("data-count");
    const badge = document.querySelector("[data-room-badge=growth]")!.getAttribute("data-count");
    expect([now, inbox, badge]).toEqual(["3", "3", "3"]);
    expect(f.to("/card/inbox")).toHaveLength(1);          // one read feeds all three
    await flush();
  });

  it("nothing is drawn from cards until the Inbox answers", async () => {
    routeFetch({ ...PEOPLE, "/card/inbox": () => ({ status: 503, body: { detail: "down" } }) });
    render(<><NowStrip workspaces={[]} /><RoomBadge workspace="growth" /></>);
    await flush();
    expect(document.querySelector("[data-now-cards]")).toBeNull();
    expect(document.querySelector("[data-room-badge]")).toBeNull();
  });
});

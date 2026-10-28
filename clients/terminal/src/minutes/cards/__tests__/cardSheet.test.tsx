/** 7q, 7p, §11.2 F1: the card sheet puts the decision block first and sticky, then gates, preview,
 *  Connected (only what exists) and History with verify links; ‹ Back and the system back close it. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { resetPeople } from "../../desktop/cardsApi";
import { resetInbox } from "../api";
import { CardSheet } from "../CardSheet";
import { card, flush, PEOPLE, routeFetch } from "./fixtures";

afterEach(() => { cleanup(); resetPeople(); resetInbox(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const full = card({
  chain: { blocks: ["c-9"], blocked_by: [] },
  decisions: [{ revision: 2, action: "acknowledge", by: "p-ana", at: "2026-10-16T10:00:00Z", credential_class: "session" }],
});

describe("CardSheet", () => {
  it("decision block first and sticky, then gates, preview, Connected, History", async () => {
    routeFetch({ ...PEOPLE, "/card/read": () => ({ body: { card: full } }), "/card/nonce": () => ({ body: { nonce: "n", content_hash: "h", revision: 3 } }) });
    render(<CardSheet id="c-1" onClose={() => {}} />);
    await waitFor(() => expect(document.querySelector("[data-decision-block]")).toBeTruthy());
    const body = document.querySelector(".vx-cs-body")!;
    const first = body.firstElementChild!;
    expect(first.classList.contains("vx-cs-sticky")).toBe(true);
    expect(first.querySelector("[data-decision-block]")).toBeTruthy();
    const order = [...body.children].map((el) => el.getAttribute("aria-label") ?? (el.classList.contains("vx-cs-sticky") ? "decision" : el.className));
    expect(order).toEqual(["decision", "Gates", "Preview", "Connected", "History"]);
    expect(screen.getByRole("link", { name: "verify" }).getAttribute("href")).toBe("/crm?record=c-1&revision=2");
    expect(screen.getByText("the chat it came from")).toBeTruthy();
    await flush();
  });

  it("only groups that have something show", async () => {
    routeFetch({ ...PEOPLE, "/card/read": () => ({ body: { card: card({ origin: null, requester: null, gates: [], summary: null }) } }), "/card/nonce": () => ({ body: { nonce: "n", content_hash: "h", revision: 3 } }) });
    render(<CardSheet id="c-1" onClose={() => {}} />);
    await waitFor(() => expect(document.querySelector("[data-decision-block]")).toBeTruthy());
    expect(screen.queryByRole("region", { name: "Connected" })).toBeNull();
    expect(screen.queryByRole("region", { name: "History" })).toBeNull();
    await flush();
  });

  it("‹ Back is top-left and closes; the system back closes too", async () => {
    routeFetch({ ...PEOPLE, "/card/read": () => ({ body: { card: full } }), "/card/nonce": () => ({ body: { nonce: "n", content_hash: "h", revision: 3 } }) });
    const close = vi.fn();
    render(<CardSheet id="c-1" onClose={close} />);
    const head = document.querySelector(".vx-cs-head")!;
    expect(head.firstElementChild!.getAttribute("aria-label")).toBe("Back");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(close).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await flush();
  });
});

/** What the browser pass on /debug/cards found (S5): the viewer is "you", never "Someone"; the sheet's
 *  preview keeps line breaks and names a locked field; the row's grid sits inside its size container;
 *  an Undo pressed in the row takes the Decline toast down. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { resetPeople } from "../../desktop/cardsApi";
import { resetInbox } from "../api";
import { CardRow } from "../CardRow";
import { CardSheet } from "../CardSheet";
import { holdDecline, undoDecline } from "../hooks";
import { decidedLine, rememberSelf, resetSelf } from "../text";
import { card, flush, PEOPLE, routeFetch } from "./fixtures";

const toastMod = vi.hoisted(() => ({ toast: vi.fn(() => "t-1"), dismissToast: vi.fn() }));
vi.mock("../../../ui-kit/primitives/Toast", () => toastMod);

afterEach(() => { cleanup(); resetPeople(); resetInbox(); resetSelf(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("lab findings", () => {
  it("the viewer's own decision reads 'by you', learned from a card where they are next actor", () => {
    const nameOf = (id: string) => ({ "p-ana": "Ana" } as Record<string, string>)[id] ?? id;
    const mine = card({ open: false, last_decision: { action: "decline", by: "p-me", at: "2026-10-17T10:00:00Z" } });
    expect(decidedLine(mine, nameOf)).toMatch(/^Declined by Someone/);
    rememberSelf([card()]);          // next_actor p-me, viewer role next_actor
    expect(decidedLine(mine, nameOf)).toMatch(/^Declined by you/);
  });

  it("the current gate names the viewer as 'you'; a locked field is named, the summary keeps its lines", async () => {
    const c = card({ summary: "- one\n+ two", face: [{ type: "locked", label: "Budget" }] });
    routeFetch({ ...PEOPLE, "/card/read": () => ({ body: { card: c } }), "/card/nonce": () => ({ body: { nonce: "n", content_hash: "h", revision: 3 } }) });
    render(<CardSheet id="c-1" onClose={() => {}} />);
    await waitFor(() => expect(document.querySelector("[data-decision-block]")).toBeTruthy());
    const current = document.querySelector('[data-gate="current"] small')!;
    expect(current.textContent).toBe("you");
    expect(document.querySelector('pre[data-preview="summary"]')!.textContent).toBe("- one\n+ two");
    expect(screen.getByText(/Budget · not visible to you/)).toBeTruthy();
    await flush();
  });

  it("the row's grid is a child of the size container", async () => {
    routeFetch({ ...PEOPLE });
    const { container } = render(<CardRow card={card()} />);
    const row = container.querySelector(".vx-crow")!;
    expect(row.firstElementChild!.classList.contains("vx-crow-grid")).toBe(true);
    await flush();
  });

  it("Undo pressed outside the toast dismisses it, and nothing is sent", () => {
    vi.useFakeTimers();
    const send = vi.fn();
    holdDecline("c-7", send, "Declined: X");
    undoDecline("c-7");
    vi.advanceTimersByTime(6000);
    expect(send).not.toHaveBeenCalled();
    expect(toastMod.dismissToast).toHaveBeenCalledWith("t-1");
  });
});

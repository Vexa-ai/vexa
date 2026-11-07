/** The decision block (7q, D13, R11): decisions carry the nonce + content hash of the face shown; a
 *  409 refetches the nonce and says the card changed; Decline is held 5 s with Undo; "Always" is
 *  shown but disabled until rule cards exist. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { resetPeople } from "../../desktop/cardsApi";
import { resetInbox, STALE_LINE } from "../api";
import { DecisionBlock } from "../DecisionBlock";
import { DECLINE_HOLD_MS } from "../hooks";
import { card, flush, PEOPLE, routeFetch } from "./fixtures";

afterEach(() => { cleanup(); resetPeople(); resetInbox(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const nonces = () => ({ "/card/nonce": (_b: Record<string, unknown>, n: number) => ({ body: { nonce: `n${n}`, content_hash: `h${n}`, revision: 3 } }) });

describe("DecisionBlock", () => {
  it("fetches the nonce when rendered and posts decide with it", async () => {
    const decided = card({ open: false, viewer: { role: "next_actor", seen: true, actions: [] }, last_decision: { action: "acknowledge", by: "p-me", at: "2026-10-17T11:00:00Z" } });
    const f = routeFetch({ ...PEOPLE, ...nonces(), "/card/decide": () => ({ body: { status: "saved", card: decided, decision: {} } }), "/card/inbox": () => ({ body: { items: [], counts: { waiting_on_me: 0, due_soon: 0, i_wait_on: 0 } } }) });
    const changed = vi.fn();
    render(<DecisionBlock card={card()} onChanged={changed} />);
    await waitFor(() => expect(f.to("/card/nonce")).toHaveLength(1));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(f.to("/card/decide")).toHaveLength(1));
    expect(f.to("/card/decide")[0].body).toEqual({ card: "c-1", nonce: "n1", content_hash: "h1", action: "acknowledge" });
    await waitFor(() => expect(changed).toHaveBeenCalledWith(decided));
  });

  it("refetches the nonce on a revision change", async () => {
    const f = routeFetch({ ...PEOPLE, ...nonces() });
    const { rerender } = render(<DecisionBlock card={card()} />);
    await waitFor(() => expect(f.to("/card/nonce")).toHaveLength(1));
    rerender(<DecisionBlock card={card({ revision: 4 })} />);
    await waitFor(() => expect(f.to("/card/nonce")).toHaveLength(2));
  });

  it("a 409 refetches the nonce and says 'This card changed — check it again'", async () => {
    const f = routeFetch({ ...PEOPLE, ...nonces(), "/card/decide": () => ({ status: 409, body: { detail: "stale nonce" } }) });
    const changed = vi.fn();
    render(<DecisionBlock card={card()} onChanged={changed} />);
    await waitFor(() => expect(f.to("/card/nonce")).toHaveLength(1));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(STALE_LINE));
    await waitFor(() => expect(f.to("/card/nonce")).toHaveLength(2));
    expect(changed).toHaveBeenCalledWith(null);
  });

  it("a 403 shows crm's one line", async () => {
    routeFetch({ ...PEOPLE, ...nonces(), "/card/decide": () => ({ status: 403, body: { detail: "A session credential is required" } }) });
    render(<DecisionBlock card={card()} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("A session credential is required"));
  });

  it("Decline shows Undo and is sent only after the window", async () => {
    const f = routeFetch({ ...PEOPLE, ...nonces(), "/card/decide": () => ({ body: { status: "saved" } }), "/card/inbox": () => ({ body: {} }) });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    render(<DecisionBlock card={card()} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Decline" }));
    expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
    await vi.advanceTimersByTimeAsync(DECLINE_HOLD_MS - 500);
    await flush();
    expect(f.to("/card/decide")).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(600);
    await flush();
    expect(f.to("/card/decide")).toHaveLength(1);
    expect(f.to("/card/decide")[0].body.action).toBe("decline");
  });

  it("Undo cancels the decline: nothing reaches crm", async () => {
    const f = routeFetch({ ...PEOPLE, ...nonces(), "/card/decide": () => ({ body: { status: "saved" } }) });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    render(<DecisionBlock card={card()} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Decline" }));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await vi.advanceTimersByTimeAsync(DECLINE_HOLD_MS * 2);
    await flush();
    expect(f.to("/card/decide")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Decline" })).toBeTruthy();
  });

  it("deliver asks for one evidence reference", async () => {
    const f = routeFetch({ ...PEOPLE, ...nonces(), "/card/decide": () => ({ body: { status: "saved" } }), "/card/inbox": () => ({ body: {} }) });
    render(<DecisionBlock card={card({ viewer: { role: "next_actor", seen: true, actions: ["deliver"] } })} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Deliver" }));
    expect(f.to("/card/decide")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Evidence"), { target: { value: "https://example.test/agenda" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(f.to("/card/decide")).toHaveLength(1));
    expect(f.to("/card/decide")[0].body).toMatchObject({ action: "deliver", evidence: "https://example.test/agenda" });
  });

  it("approvals show the three-way ask; Always is disabled until rules exist", async () => {
    routeFetch({ ...PEOPLE, ...nonces() });
    render(<DecisionBlock card={card({ kind: "approval", viewer: { role: "next_actor", seen: true, actions: ["decide", "decline"] } })} />);
    const labels = [...document.querySelectorAll(".vx-cs-acts .vx-btn")].map((b) => b.textContent);
    expect(labels.slice(0, 3)).toEqual(["Approve", "Always", "Decline"]);
    const always = screen.getByRole("button", { name: /Always/ }) as HTMLButtonElement;
    expect(always.disabled).toBe(true);
    expect(always.title).toBe("Rules come later");
    await flush();
  });

  it("renders nothing for a viewer with no actions", () => {
    routeFetch({ ...PEOPLE });
    const { container } = render(<DecisionBlock card={card({ viewer: { role: "watcher", seen: true, actions: [] } })} />);
    expect(container.innerHTML).toBe("");
  });
});

/** `/decide/<card>` — the nudge landing (0.13.4 S6).
 *
 *    signed out      no card data before sign-in (PRD M4/§16): the card is never even read
 *    carried link    one Continue → the redeem, carrying next=/decide/<card>; nothing spent on load
 *    no link         the email form asks for a link back to this card and says "Check your email"
 *    signed in       the full preview (every field) comes BEFORE the decision row; `seen` once
 *    decided         Done, "N more waiting on you.", Next: <the next card>
 *    unreadable      one sentence, no details */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { DecideLanding } from "../decide/DecideLanding";
import { actionsOf, redeemHref, viewOf, waitingAfter } from "../decide/decideState";

const CARD = {
  id: "c-210", revision: 4, title: "Interview guide reviewed", kind: "commitment", type: "commitment",
  fields: { who: "Pat Doe", what: "Review the interview guide so the interviews can start", date: "Thu 15 Oct", visibility: "Growth members" },
  suggestion: "Morning triage suggests Sign: the next card waits on it.",
  actions: [{ id: "sign", label: "Sign", transition: "signed" }, { id: "decline", label: "Decline", transition: "declined" }],
  workspace_name: "Growth", from: "Sam Lee", created_at: Date.now() - 14 * 3600_000, state: "open", states: ["open", "signed"], side_states: [],
  can: { edit: true, transition: true, review: false, members: false }, href: "/",
};

type Route = { status: number; body: unknown };
function stubFetch(routes: Record<string, Route | ((body: unknown) => Route)>) {
  const calls: { url: string; body: unknown }[] = [];
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, body });
    const r = routes[url];
    const out = typeof r === "function" ? r(body) : r ?? { status: 404, body: {} };
    return new Response(JSON.stringify(out.body), { status: out.status, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", f);
  return calls;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("signed out", () => {
  it("shows no card data and never reads the card", async () => {
    const calls = stubFetch({ "/api/auth/me": { status: 401, body: { authenticated: false } }, "/api/desktop/card/read": { status: 200, body: { card: CARD } } });
    const { container } = render(<DecideLanding cardId="c-210" carried="" />);
    await screen.findByText("Sign in to open this card");
    expect(container.textContent).not.toContain("Interview guide");
    expect(container.textContent).not.toContain("Sam Lee");
    expect(calls.some((c) => c.url.startsWith("/api/desktop/card/"))).toBe(false);
  });

  it("a carried one-time link: one Continue to the redeem with next, spent only by the click", async () => {
    const calls = stubFetch({ "/api/auth/me": { status: 401, body: {} } });
    render(<DecideLanding cardId="c-210" carried="tok123" />);
    const go = await screen.findByText("Continue");
    expect(go.closest("a")?.getAttribute("href")).toBe("/api/auth/redeem?t=tok123&next=%2Fdecide%2Fc-210");
    expect(calls.some((c) => c.url.includes("redeem"))).toBe(false);
    expect(redeemHref("a b", "x/y")).toBe("/api/auth/redeem?t=a%20b&next=%2Fdecide%2Fx%252Fy");
  });

  it("no link: asks for a sign-in link that returns here, and answers Check your email", async () => {
    const calls = stubFetch({ "/api/auth/me": { status: 401, body: {} }, "/api/auth/request-link": { status: 200, body: { ok: true } } });
    render(<DecideLanding cardId="c-210" carried="" />);
    const input = await screen.findByLabelText("Email");
    fireEvent.change(input, { target: { value: "pat@example.com" } });
    fireEvent.click(screen.getByText("Email me a sign-in link"));
    await screen.findByText("Check your email");
    expect(calls.find((c) => c.url === "/api/auth/request-link")?.body).toEqual({ email: "pat@example.com", next: "/decide/c-210" });
  });
});

describe("signed in", () => {
  const me = { status: 200, body: { authenticated: true, email: "pat@example.com" } };

  it("renders the full preview before the decisions, the rule on its own line, and calls seen once", async () => {
    const calls = stubFetch({ "/api/auth/me": me, "/api/desktop/card/read": { status: 200, body: { card: CARD } }, "/api/desktop/card/seen": { status: 200, body: {} } });
    const { container } = render(<DecideLanding cardId="c-210" carried="" />);
    await screen.findByText("Interview guide reviewed");
    for (const v of ["Pat Doe", "Review the interview guide so the interviews can start", "Thu 15 Oct", "Growth members"]) expect(screen.getByText(v)).toBeTruthy();
    expect(screen.getByText("Commitment")).toBeTruthy();
    const preview = container.querySelector("[data-preview]")!;
    const row = container.querySelector("[data-decisions]")!;
    const rule = container.querySelector("[data-rule]")!;
    expect(preview.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(rule.parentElement).toBe(row.parentElement);
    expect(rule.textContent).toContain("Morning triage suggests");
    expect(container.textContent).toContain("Growth · from Sam Lee · 14 h ago");
    expect(screen.getByText("Sign out")).toBeTruthy();
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/desktop/card/seen")).toHaveLength(1));
    expect(calls.find((c) => c.url === "/api/desktop/card/seen")?.body).toEqual({ card: "c-210" });
  });

  it("deciding shows Done, how many more wait, and Next: <card>", async () => {
    const calls = stubFetch({
      "/api/auth/me": me,
      "/api/desktop/card/read": { status: 200, body: { card: CARD } },
      "/api/desktop/card/seen": { status: 200, body: {} },
      "/api/desktop/card/update": { status: 200, body: { card: { ...CARD, state: "signed" } } },
      "/api/desktop/card/list": { status: 200, body: { cards: [
        { id: "c-210", title: "Interview guide reviewed", state: "signed" },
        { id: "c-300", title: "Approve the newsletter CTA", state: "open" },
        { id: "c-301", title: "Old one", state: "done" },
        { id: "c-302", title: "Pricing page copy", state: "open" },
      ] } },
    });
    render(<DecideLanding cardId="c-210" carried="" />);
    fireEvent.click(await screen.findByText("Sign"));
    await screen.findByText("Done");
    expect(calls.find((c) => c.url === "/api/desktop/card/update")?.body).toEqual({ card: "c-210", expected_revision: 4, transition: "signed" });
    expect(calls.find((c) => c.url === "/api/desktop/card/list")?.body).toEqual({ mine: true });
    expect(screen.getByText("2 more waiting on you.")).toBeTruthy();
    const next = screen.getByText("Next: Approve the newsletter CTA");
    expect(next.getAttribute("href")).toBe("/decide/c-300");
    expect(screen.getByText("Open the Inbox").getAttribute("href")).toBe("/");
  });

  it("a card this person cannot read answers one sentence, no details", async () => {
    stubFetch({ "/api/auth/me": me, "/api/desktop/card/read": { status: 404, body: { detail: "card c-999 not visible to subject 42" } } });
    const { container } = render(<DecideLanding cardId="c-999" carried="" />);
    await screen.findByText("There is no card here for you.");
    expect(container.textContent).not.toContain("subject");
    expect(container.querySelector("[data-landing=absent]")).toBeTruthy();
  });

  it("a refused decision says one line and keeps the card", async () => {
    stubFetch({ "/api/auth/me": me, "/api/desktop/card/read": { status: 200, body: CARD }, "/api/desktop/card/seen": { status: 404, body: {} },
      "/api/desktop/card/update": { status: 403, body: { detail: "nope" } } });
    render(<DecideLanding cardId="c-210" carried="" />);
    fireEvent.click(await screen.findByText("Decline"));
    await screen.findByText("You can't decide this card.");
    expect(screen.getByText("Interview guide reviewed")).toBeTruthy();
  });
});

describe("decideState", () => {
  it("falls back to the declared stages when crm sends no actions, and to none without the right", () => {
    const { actions: _a, ...bare } = CARD;
    expect(actionsOf(bare as Record<string, unknown>)).toEqual([{ id: "signed", label: "Signed", transition: "signed" }]);
    expect(actionsOf({ ...bare, can: { transition: false } })).toEqual([]);
  });
  it("tolerates a minimal card", () => {
    const v = viewOf({ id: "x", title: "T", summary: "Do it", due: "Fri", workspaces: ["Ops"], revision: 1 });
    expect(v.fields.map((f) => f.label)).toEqual(["What", "Date", "Visible to"]);
    expect(v.kind).toBe("");
  });
  it("drops cards that wait on someone else when the server names the actor", () => {
    const list = { cards: [{ id: "a", title: "A", next_actor: "u1" }, { id: "b", title: "B", next_actor: "u2" }, { id: "c", title: "C" }] };
    expect(waitingAfter(list, "z", { id: "u1" }).map((c) => c.id)).toEqual(["a", "c"]);
  });
});

describe("R11 on the landing: a destructive decision offers Undo", () => {
  it("names decline/reject/cancel as destructive, sign and approve as not", async () => {
    const { isDestructive } = await import("../decide/decideState");
    expect(isDestructive({ id: "decline", transition: "declined" })).toBe(true);
    expect(isDestructive({ id: "x", transition: "rejected" })).toBe(true);
    expect(isDestructive({ id: "sign", transition: "signed" })).toBe(false);
    expect(isDestructive({ id: "approve" })).toBe(false);
  });
});

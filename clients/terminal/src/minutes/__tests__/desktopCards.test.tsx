/** CARDS ON THE DESKTOP (#1783, desktop ADR D2; UI decisions 15c, 15d; ADR §2.10 explicit UI state).
 *
 *  Pinned here: behind `desktop_cards` (off → no Cards section, no card call, no Make a card); a card
 *  tile draws its face from the resolved blocks (state chip, stepper, next, waiting, latest movement,
 *  stalled, "n of m sub-cards done", members, New, a locked field as "Request access"); opening it
 *  reads `/card/read` and shows the description, activity, sub-cards and members, moves the stage only
 *  to a declared state with the revision it read, and adds a member; an agent's proposal is a quiet
 *  "Vexa suggests" row whose Add / Not now review it; an invite is a row whose Accept / Decline answer
 *  `/card/membership`; Take off my desktop removes `card:<id>` through the arrange path with Undo; and
 *  "Make a card" on a chat or a page creates a card about it and confirms with Open. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const toasts: { message: string; undo?: () => void; action?: { label: string; onClick: () => void } }[] = [];
vi.mock("../../ui-kit/primitives/Toast", () => ({
  toast: (message: string, opts?: { undo?: () => void; action?: { label: string; onClick: () => void } }) => {
    toasts.push({ message, undo: opts?.undo, action: opts?.action });
  },
  Toaster: () => null,
}));

import { setFeatureOverride } from "../../platform/features";
import type { CardView, Desktop } from "../../platform/desktop";
import { DesktopHome, DesktopToaster } from "../desktop/DesktopHome";
import { resetDesktopStore } from "../desktop/desktopApi";
import { useCardItems, type CardTarget } from "../desktop/useCardItems";

const card = (over: Partial<CardView> = {}): CardView => ({
  id: "r1", ref: "card:r1", type: "Card", revision: 3, title: "Pricing proposal",
  about: { kind: "page", ref: "page:alpha/pricing.md" },
  state: "Doing", states: ["To do", "Doing", "Done"], side_states: ["Dropped"],
  next: "Send the revised numbers", waiting: "Legal review", summary: "Second round after Tuesday.", due: "2026-10-22", progress: 0.5,
  workspaces: ["alpha"], parent: null,
  children: { done: 1, total: 2, items: [{ id: "r2", title: "Draft numbers", state: "Done", done: true }, { id: "r3", title: "Legal check", state: "Doing", done: false }] },
  members: [{ subject: "7", status: "member" }, { subject: "8", status: "invited" }],
  face: [{ type: "stepper" }, { type: "next" }, { type: "waiting" }, { type: "metric", label: "Amount", value: "12,000" },
    { type: "locked", label: "Margin" }, { type: "movement" }, { type: "subcards" }, { type: "people" }, { type: "teleport" }],
  movement: { by: "7", kind: "agent", what: "Moved to Doing", at: "2026-10-15T09:00:00Z" }, stalled: true,
  can: { edit: true, transition: true, review: true, members: true }, href: "/crm?record=r1",
  ...over,
});

const sec = (id: string, over: Partial<Desktop["sections"][number]> = {}) => ({
  id, title: id[0].toUpperCase() + id.slice(1), hidden: false, collapsed: false, column: 1, span: 1, size: "normal", items: [], ...over,
});

function desktop(over: Partial<Desktop> = {}): Desktop {
  return {
    version: 5, undo_version: 4, saved: [], proposals: [], filters: [], hidden_workspaces: 0, presence_visibility: "off",
    cards_on: true,
    sections: [
      sec("workspaces", { items: [{ kind: "workspace", ref: "workspace:alpha", slug: "alpha", id: "w1", name: "Alpha project", workspace_kind: "shared", access: "owner", write: true, members: [] },
        { kind: "workspace", ref: "workspace:beta", slug: "beta", id: "w2", name: "Beta notes", workspace_kind: "shared", access: "reader", write: false, members: [] }] }),
      sec("agents"), sec("files"),
      sec("cards", { items: [{ kind: "card", ...card() }] }),
    ],
    card_proposals: [{ id: "cp1", kind: "create", title: "Follow up on pricing", workspace: "alpha", fields: {}, reason: "you said you would follow up",
      author_kind: "agent", at: "2026-10-15T10:00:00Z" }],
    card_invites: [card({ id: "r9", ref: "card:r9", title: "Event logistics", members: [{ subject: "9", status: "member" }, { subject: "7", status: "invited" }] })],
    ...over,
  };
}

type Call = { url: string; body: unknown };
let calls: Call[] = [];
let answers: Record<string, Array<{ status: number; body: unknown }>> = {};
let current: Desktop;

function route(url: string, init?: RequestInit) {
  const body = init?.body ? JSON.parse(String(init.body)) : undefined;
  calls.push({ url, body });
  const q = answers[url];
  if (q && q.length) { const a = q.shift()!; return Promise.resolve(new Response(JSON.stringify(a.body), { status: a.status })); }
  if (url === "/api/desktop") return Promise.resolve(new Response(JSON.stringify(current), { status: 200 }));
  if (url === "/api/desktop/card/read") return Promise.resolve(new Response(JSON.stringify({ ...card(), activity: [
    { by: "7", kind: "agent", what: "Moved to Doing", at: "2026-10-15T09:00:00Z" },
    { by: "8", kind: "person", what: "Made the card", at: "2026-10-14T16:00:00Z" },
  ] }), { status: 200 }));
  if (url.startsWith("/api/desktop/card/")) return Promise.resolve(new Response(JSON.stringify({ status: "saved", card: card({ id: "r5", title: "New one" }), members: [] }), { status: 200 }));
  if (url.startsWith("/api/people")) return Promise.resolve(new Response(JSON.stringify({ people: [
    { subject: "7", name: "Sam Example" }, { subject: "8", name: "Kim Sample" }, { subject: "11", name: "Lee Person" }] }), { status: 200 }));
  return Promise.resolve(new Response(JSON.stringify({ status: "applied", version: current.version + 1, undo_version: current.version, summary: "Took Pricing proposal off your desktop" }), { status: 200 }));
}
const posts = (path: string) => calls.filter((c) => c.url === path);
const cardCalls = () => calls.filter((c) => c.url.startsWith("/api/desktop/card/"));

beforeEach(() => {
  calls = []; answers = {}; toasts.length = 0; current = desktop();
  vi.stubGlobal("fetch", vi.fn(route));
  setFeatureOverride("all");
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); resetDesktopStore(); setFeatureOverride("all"); });

const home = () => render(<><DesktopHome active greeting={{ title: "Hi" }} fallback={<div data-testid="old-home">old</div>} /><DesktopToaster /></>);
const cardsLoaded = async () => { home(); return screen.findByRole("region", { name: "Cards" }); };
const tile = () => document.querySelector("[data-card-tile=r1]") as HTMLElement;

describe("behind desktop_cards", () => {
  it("off (the desk read carries no cards): no Cards section, no pending rows, no card call", async () => {
    current = desktop({ cards_on: false, card_proposals: undefined, card_invites: undefined,
      sections: [...desktop().sections.slice(0, 3), sec("cards", { hidden: true })] });
    setFeatureOverride({ desktop_cards: false });
    home();
    await screen.findByRole("region", { name: "Workspaces" });
    expect(document.querySelector("[data-desk-section=cards]")).toBeNull();
    expect(document.querySelector("[data-card-proposal]")).toBeNull();
    expect(cardCalls()).toHaveLength(0);
  });
});

describe("a card tile", () => {
  it("draws its face from the resolved blocks", async () => {
    await cardsLoaded();
    const t = tile();
    expect(t.querySelector("[data-card-state]")!.textContent).toBe("Doing");
    expect(t.querySelector("[data-face=stepper] [data-current]")!.textContent).toBe("Doing");
    expect(t.textContent).toContain("Next: Send the revised numbers");
    expect(t.textContent).toContain("Waiting on Legal review");
    expect(t.querySelector("[data-face=metric]")!.textContent).toContain("12,000");
    // a field this viewer cannot read draws nothing: no access-request route exists yet, so no dead text
    expect(t.querySelector("[data-face=locked]")).toBeNull();
    expect(t.textContent).not.toContain("Request access");
    expect(t.textContent).not.toContain("Margin");
    expect(t.querySelector("[data-face=movement]")!.textContent).toContain("Moved to Doing");
    expect(t.querySelector("[data-stalled-chip]")).toBeTruthy();
    expect(t.textContent).toContain("1 of 2 sub-cards done");
    expect(t.querySelectorAll("[data-member]").length).toBe(2);
    expect(t.querySelector("[data-face=teleport]")).toBeNull();
    expect(t.querySelector("[data-new-chip]")).toBeNull();
  });

  it("says New when the server marks it new, and a card without a stored face gets the default face", async () => {
    current = desktop({ sections: [...desktop().sections.slice(0, 3), sec("cards", { items: [{ kind: "card", ...card({ face: [], new: true }) }] })] });
    await cardsLoaded();
    expect(tile().querySelector("[data-new-chip]")).toBeTruthy();
    expect(tile().querySelector("[data-face=stepper]")).toBeTruthy();
    expect(tile().querySelector("[data-face=metric]")).toBeNull();
  });

  it("Take off my desktop removes card:<id> through the arrange path, with Undo", async () => {
    await cardsLoaded();
    fireEvent.click(within(tile()).getByRole("button", { name: "More for Pricing proposal" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /Take off my desktop/ }));
    await waitFor(() => expect(posts("/api/desktop/arrange")).toHaveLength(1));
    expect(posts("/api/desktop/arrange")[0].body).toEqual({ ops: [{ op: "remove", ref: "card:r1" }], base_version: 5 });
    await waitFor(() => expect(toasts.some((t) => t.undo)).toBe(true));
  });
});

describe("the card opened", () => {
  const open = async () => {
    await cardsLoaded();
    fireEvent.click(within(tile()).getByRole("button", { name: "Open card Pricing proposal" }));
    return screen.findByRole("dialog", { name: "Pricing proposal" });
  };

  it("opening a New card reads it (crm-api marks it seen) and reloads the desktop so New clears", async () => {
    const withCard = (isNew: boolean) => {
      const d = desktop();
      return { ...d, sections: d.sections.map((s) => (s.id === "cards" ? { ...s, items: [{ kind: "card", ...card({ new: isNew }) }] } : s)) } as Desktop;
    };
    current = withCard(true);
    await cardsLoaded();
    expect(tile().querySelector("[data-new-chip]")).toBeTruthy();
    const before = calls.filter((c) => c.url === "/api/desktop").length;
    current = withCard(false);
    fireEvent.click(within(tile()).getByRole("button", { name: "Open card Pricing proposal" }));
    await waitFor(() => expect(posts("/api/desktop/card/read")).toHaveLength(1));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/desktop").length).toBeGreaterThan(before));
    await waitFor(() => expect(tile().querySelector("[data-new-chip]")).toBeNull());
  });

  it("reads /card/read and shows its description, activity, sub-cards and members", async () => {
    const dlg = await open();
    await waitFor(() => expect(posts("/api/desktop/card/read")).toHaveLength(1));
    expect(posts("/api/desktop/card/read")[0].body).toEqual({ card: "r1" });
    await within(dlg).findByText("Second round after Tuesday.");
    expect(dlg.querySelectorAll("[data-card-activity]").length).toBe(2);
    expect(dlg.textContent).toContain("Made the card");
    expect(dlg.querySelectorAll("[data-subcard]").length).toBe(2);
    expect(dlg.querySelector("[data-card-member='8']")!.textContent).toContain("invited");
  });

  it("moves the stage only to a declared state, with the revision it read", async () => {
    const dlg = await open();
    await within(dlg).findByText("Second round after Tuesday.");
    const stages = [...dlg.querySelectorAll("[data-stage]")].map((b) => b.getAttribute("data-stage"));
    expect(stages).toEqual(["To do", "Doing", "Done", "Dropped"]);
    fireEvent.click(within(dlg).getByRole("button", { name: "Move to Done" }));
    await waitFor(() => expect(posts("/api/desktop/card/update")).toHaveLength(1));
    expect(posts("/api/desktop/card/update")[0].body).toEqual({ card: "r1", expected_revision: 3, transition: "Done" });
  });

  it("offers no stage move to a viewer who may not transition", async () => {
    answers["/api/desktop/card/read"] = [{ status: 200, body: { ...card({ can: { edit: false, transition: false, review: false, members: false } }), activity: [] } }];
    const dlg = await open();
    await within(dlg).findByText("Second round after Tuesday.");
    expect(within(dlg).queryByRole("button", { name: "Move to Done" })).toBeNull();
    expect(within(dlg).queryByRole("button", { name: "Add member" })).toBeNull();
  });

  it("adds a member by subject", async () => {
    const dlg = await open();
    await within(dlg).findByText("Second round after Tuesday.");
    fireEvent.change(within(dlg).getByRole("combobox", { name: "Person to add" }), { target: { value: "11" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Add member" }));
    await waitFor(() => expect(posts("/api/desktop/card/members")).toHaveLength(1));
    expect(posts("/api/desktop/card/members")[0].body).toEqual({ card: "r1", subject: "11", add: true });
  });
});

describe("what waits for the person", () => {
  it("an agent's proposal is a quiet row: who and why; Add accepts, Not now rejects", async () => {
    await cardsLoaded();
    const row = document.querySelector("[data-card-proposal=cp1]") as HTMLElement;
    expect(row.textContent).toContain("Vexa suggests");
    expect(row.textContent).toContain("Follow up on pricing");
    expect(row.textContent).toContain("you said you would follow up");
    fireEvent.click(within(row).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(posts("/api/desktop/card/review")).toHaveLength(1));
    expect(posts("/api/desktop/card/review")[0].body).toEqual({ proposal_id: "cp1", accept: true });
    fireEvent.click(within(row).getByRole("button", { name: "Not now" }));
    await waitFor(() => expect(posts("/api/desktop/card/review")).toHaveLength(2));
    expect(posts("/api/desktop/card/review")[1].body).toEqual({ proposal_id: "cp1", accept: false });
  });

  it("an invite is a row: Accept and Decline answer /card/membership", async () => {
    await cardsLoaded();
    const row = document.querySelector("[data-card-invite=r9]") as HTMLElement;
    expect(row.textContent).toContain("added you to");
    expect(row.textContent).toContain("Event logistics");
    fireEvent.click(within(row).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(posts("/api/desktop/card/membership")).toHaveLength(1));
    expect(posts("/api/desktop/card/membership")[0].body).toEqual({ card: "r9", accept: true });
    fireEvent.click(within(row).getByRole("button", { name: "Decline" }));
    await waitFor(() => expect(posts("/api/desktop/card/membership")).toHaveLength(2));
    expect(posts("/api/desktop/card/membership")[1].body).toEqual({ card: "r9", accept: false });
  });
});

function MakeProbe({ target }: { target: CardTarget }) {
  const items = useCardItems()(target);
  return <div>{items.map((i) => <button key={i.id} onClick={() => void i.onSelect()}>{i.label}</button>)}</div>;
}

describe("Make a card on a chat or a page", () => {
  it("a page in a workspace the person can write: about page:<ws>/<path>, bound to that workspace; confirms with Open", async () => {
    render(<><MakeProbe target={{ kind: "page", slug: "alpha", path: "docs/plan.md", title: "Plan" }} /><DesktopToaster /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Make a card" }));
    await waitFor(() => expect(posts("/api/desktop/card/create")).toHaveLength(1));
    expect(posts("/api/desktop/card/create")[0].body).toEqual({ title: "Plan", about: "page:alpha/docs/plan.md", workspace: "alpha" });
    await waitFor(() => expect(toasts.find((t) => t.action)?.action?.label).toBe("Open"));
    toasts.find((t) => t.action)!.action!.onClick();
    expect(await screen.findByRole("dialog", { name: "Pricing proposal" })).toBeTruthy();
  });

  it("a page in a workspace the person only reads: no workspace", async () => {
    render(<MakeProbe target={{ kind: "page", slug: "beta", path: "x.md", title: "X" }} />);
    fireEvent.click(await screen.findByRole("button", { name: "Make a card" }));
    await waitFor(() => expect(posts("/api/desktop/card/create")).toHaveLength(1));
    expect(posts("/api/desktop/card/create")[0].body).toEqual({ title: "X", about: "page:beta/x.md" });
  });

  it("a chat: about chat:<id>, titled as the chat", async () => {
    render(<MakeProbe target={{ kind: "chat", id: "pchat-1", title: "Weekly sync" }} />);
    fireEvent.click(await screen.findByRole("button", { name: "Make a card" }));
    await waitFor(() => expect(posts("/api/desktop/card/create")).toHaveLength(1));
    expect(posts("/api/desktop/card/create")[0].body).toEqual({ title: "Weekly sync", about: "chat:pchat-1" });
  });

  it("is not offered with desktop_cards off", async () => {
    setFeatureOverride({ desktop_cards: false });
    render(<MakeProbe target={{ kind: "chat", id: "pchat-1", title: "Weekly sync" }} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("button", { name: "Make a card" })).toBeNull();
  });
});

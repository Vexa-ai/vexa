/** 0.13.4 S4 — rooms and tagging in the client: the tag card (D21), the members panel, the room header
 *  as 7z/7hh draw it (people pill · Start call · Invite · ⋯ with the two switches), the room of two's
 *  header, the consent card, and the fold below 520px. The server decides; these check what is drawn
 *  and what one click sends. Synthetic people only. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../CallButton", () => ({ CallButton: () => <button type="button">Start call</button> }));

import { RoomHeader } from "../rooms/RoomHeader";
import { MembersPanel } from "../rooms/MembersPanel";
import { switchItems } from "../rooms/roomSwitches";
import { TagCard } from "../../surfaces/TagCard";
import { checkRoomTags } from "../../surfaces/shareFlow";
import type { RoomSettings } from "../../surfaces/roomsApi";
import type { RoomSwitches, TagAnswer } from "../../surfaces/roomPeopleApi";

const room = (over: Partial<RoomSettings> = {}): RoomSettings => ({ session_id: "s_1", room: true, humans: 2, visibility: "closed", proactivity: "quiet", workspace: "w1", ...over });
const okJson = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) }) as unknown as Response;
const noop = () => {};

const PEOPLE = { session_id: "s_1", you: "u_me", workspace: "w1", workspace_name: "Harbor AI Meetup", visibility: "closed", pair: false,
  members: [{ principal: "u_me", name: "Dmitry Grankin", role: "owner", kind: "human", derived: false, opened: 1_760_000_000 },
    { principal: "u_ines", name: "Ines Duarte", role: "member", kind: "human", derived: false, opened: null, added_by_name: "Dmitry Grankin" }],
  invited: [], workspace_others: [{ principal: "u_rita", name: "Rita Lopes" }] };
const SWITCHES: RoomSwitches = { session_id: "s_1", vexa: true, transcribe: true, asks: {}, pair: false, can_switch: true,
  locks: { vexa: { on: null, off: null }, transcribe: { on: null, off: null } } };

let routes: Record<string, unknown>;
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  routes = { "/people": PEOPLE, "/switches": SWITCHES };
  fetchMock = vi.fn(async (url: string) => {
    const hit = Object.keys(routes).find((k) => String(url).includes(k));
    return okJson(hit ? routes[hit] : room());
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const card = (over: Partial<TagAnswer> = {}): TagAnswer => ({
  case: "workspace_member", subject: "u_ines", name: "Ines Duarte", workspace: "w1", workspace_name: "Harbor AI Meetup",
  card: { kind: "invitation", case: "workspace_member", only_you: true, title: "Add Ines Duarte to #test and tell them?",
    who: "Ines Duarte · in Harbor AI Meetup, not in #test", gets: "#test and its history", actions: ["add", "always", "mention"],
    always_line: "In Harbor AI Meetup, when I @mention a workspace member who isn't in the room, add them and tell them." },
  ...over });

describe("the tag card (D21)", () => {
  it("a workspace member: the three-way ask, one primary, only you see it", () => {
    render(<TagCard session="s_1" answer={card()} />);
    expect(screen.getByText("only you see this")).toBeTruthy();
    const acts = screen.getAllByRole("button").map((b) => b.textContent);
    expect(acts).toEqual(["Add and tell Ines", "Yes, and always for Harbor AI Meetup", "Just mention"]);
    expect(document.querySelectorAll('[data-variant="primary"]').length).toBe(1);
  });
  it("Add and tell sends one click and folds the card", async () => {
    routes["/members"] = { case: "workspace_member", added: true, rang: ["u_ines"] };
    render(<TagCard session="s_1" answer={card()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add and tell Ines" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Added Ines · ringing into the call"));
    const [url, init] = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/members"))! as [string, RequestInit];
    expect(url).toBe("/api/rooms/s_1/members");
    expect(JSON.parse(String(init.body))).toMatchObject({ subject: "u_ines", always: false, origin: "person" });
  });
  it("Always, not applied, says the owner decides", async () => {
    routes["/members"] = { case: "workspace_member", added: true, always: { applied: false, decides: "u_owner" } };
    render(<TagCard session="s_1" answer={card()} />);
    fireEvent.click(screen.getByRole("button", { name: /always for/ }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("owner to approve"));
  });
  it("Just mention changes nothing and folds", () => {
    render(<TagCard session="s_1" answer={card()} />);
    fireEvent.click(screen.getByRole("button", { name: "Just mention" }));
    expect(screen.getByRole("status").textContent).toContain("isn't told");
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/members"))).toBe(false);
  });
  it("anyone else: an invite with no “always”, and why", () => {
    render(<TagCard session="s_1" answer={card({ case: "known", subject: undefined, email: "…@gmail.example", card: {
      kind: "invitation", case: "known", only_you: true, title: "Invite Nastya to Harbor AI Meetup and #test, and tell them?",
      who: "…@gmail.example", gets: "Harbor AI Meetup as a contributor", actions: ["invite", "mention"],
      never_always: "No “always” here: bringing someone new into a workspace is decided person by person." } })} />);
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Invite and tell them", "Just mention"]);
    expect(screen.getByText(/decided person by person/)).toBeTruthy();
  });
  it("added by the tagger's own line: a receipt with Undo", async () => {
    routes["/members/u_dan"] = { removed: true };
    render(<TagCard session="s_1" answer={{ case: "added_by_rule", subject: "u_dan", name: "Dan Reis" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("taken out again"));
    expect((fetchMock.mock.calls.find((c) => String(c[0]).includes("/members/u_dan"))![1] as RequestInit).method).toBe("DELETE");
  });
  it("after a room message, only people outside the room yield a card", async () => {
    const api = { tagPerson: vi.fn(async (_s: string, who: { subject?: string }) => who.subject === "u_ines"
      ? { case: "member", card: null } as TagAnswer : card({ subject: who.subject })) };
    const out = await checkRoomTags("s_1", [{ subject: "u_ines", label: "Ines", name: "Ines" }, { subject: "u_rita", label: "Rita", name: "Rita" }], api);
    expect(out.map((a) => a.subject)).toEqual(["u_rita"]);
  });
});

describe("the room header (7z)", () => {
  it("# name · workspace chip · people pill · Start call · Invite · ⋯ with both switches", async () => {
    render(<RoomHeader session="s_1" label="test" room={room()} workspaceName="Harbor AI Meetup" onLeave={noop} onThinkPrivately={noop} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /2 members/ })).toBeTruthy());
    await waitFor(() => expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/switches"))).toBe(true));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByRole("button", { name: "Start call" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Invite/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "More for this room" }));
    const labels = [...screen.queryAllByRole("menuitemcheckbox"), ...screen.queryAllByRole("menuitemradio"), ...screen.queryAllByRole("menuitem")]
      .map((m) => m.textContent ?? "");
    expect(labels.some((l) => l.includes("Vexa in this room"))).toBe(true);
    expect(labels.some((l) => l.includes("Transcribe calls"))).toBe(true);
  });
  it("Vexa off shows a quiet indicator by the name", async () => {
    routes["/switches"] = { ...SWITCHES, vexa: false };
    render(<RoomHeader session="s_1" label="test" room={room()} workspaceName="Harbor AI Meetup" onLeave={noop} onThinkPrivately={noop} />);
    await waitFor(() => expect(document.querySelector('[data-indicator="vexa-off"]')).toBeTruthy());
  });
  it("a room of two: the other person's face and name, no workspace chip, no Invite", async () => {
    routes["/people"] = { ...PEOPLE, pair: true, visibility: "open" };
    routes["/switches"] = { ...SWITCHES, pair: true };
    render(<RoomHeader session="s_1" label="" room={room({ visibility: "open", workspace: "pair-abc" })} workspaceName="" onLeave={noop} onThinkPrivately={noop} />);
    await waitFor(() => expect(document.querySelector("[data-room-of-two]")).toBeTruthy());
    expect(screen.getByText("Ines Duarte")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Invite/ })).toBeNull();
    expect(document.querySelector("[data-ws-chip]")).toBeNull();
  });
  it("the other person's turn-on ask is a card: Turn on · Keep off", async () => {
    routes["/switches"] = { ...SWITCHES, pair: true, vexa: false,
      asks: { vexa: { by: "u_ines", by_name: "Ines Duarte", mine: false, at: 1 } } };
    routes["/people"] = { ...PEOPLE, pair: true };
    render(<RoomHeader session="s_1" label="" room={room()} workspaceName="" onLeave={noop} onThinkPrivately={noop} />);
    await waitFor(() => expect(document.querySelector('[data-turn-on-card="vexa"]')).toBeTruthy());
    expect(screen.getByRole("button", { name: "Turn on" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Keep off" })).toBeTruthy();
  });
});

describe("the switches in ⋯ (7hh)", () => {
  it("a lock says why and cannot be clicked; a member of a bigger room sees the state", () => {
    const locked = switchItems({ ...SWITCHES, locks: { ...SWITCHES.locks, transcribe: { on: null, off: "team.calls-have-notes@abc" } } }, noop);
    const tr = locked.find((i) => i.key === "sw-transcribe")!;
    expect(String(tr.label)).toContain("locked by policy (team.calls-have-notes)");
    expect(tr.disabled).toBe(true);
    const member = switchItems({ ...SWITCHES, can_switch: false }, noop);
    expect(String(member[0].label)).toContain("owners change this");
  });
  it("in a room of two, off then on is an ask", () => {
    const off = switchItems({ ...SWITCHES, pair: true, vexa: false }, noop);
    expect(String(off[0].label)).toContain("ask to turn on");
  });
});

describe("the members panel", () => {
  it("lists members with how they got there, the workspace's others with Add, and never an address", async () => {
    render(<MembersPanel session="s_1" label="test" open onClose={noop} />);
    await waitFor(() => expect(screen.getByText("Ines Duarte")).toBeTruthy());
    expect(screen.getByText(/added by Dmitry Grankin · not opened yet/)).toBeTruthy();
    expect(screen.getByText("Rita Lopes")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add" })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/@/);
  });
});

describe("on a phone (PRD §11.3 fix 6)", () => {
  it("below 520px the header is name · people · ⋯, and Start call, Invite and both switches sit in ⋯", async () => {
    class RO { cb: ResizeObserverCallback; constructor(cb: ResizeObserverCallback) { this.cb = cb; }
      observe() { this.cb([{ contentRect: { width: 390 } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver); }
      disconnect() {} unobserve() {} }
    vi.stubGlobal("ResizeObserver", RO);
    render(<RoomHeader session="s_1" label="test" room={room()} workspaceName="Harbor AI Meetup" onLeave={noop} onThinkPrivately={noop} />);
    await waitFor(() => expect(document.querySelector("[data-room-header][data-narrow]")).toBeTruthy());
    await waitFor(() => expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/switches"))).toBe(true));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("button", { name: /^Invite$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start call" })).toBeNull();
    expect(document.querySelector("[data-ws-chip]")).toBeNull();
    expect(screen.getByRole("button", { name: /members/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "More for this room" }));
    const labels = [...screen.queryAllByRole("menuitemradio"), ...screen.queryAllByRole("menuitem")].map((m) => m.textContent ?? "");
    for (const want of ["Start call", "Invite…", "Vexa in this room", "Transcribe calls"]) expect(labels.some((l) => l.includes(want))).toBe(true);
    vi.unstubAllGlobals();
  });
});

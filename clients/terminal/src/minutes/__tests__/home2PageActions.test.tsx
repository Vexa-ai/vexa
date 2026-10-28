/** A PAGE ACTION RUNS WHAT ITS CONFIRM NAMES, AND THE CHAT SHOWS WHAT WAS SENT (#1783, R3 `home2`).
 *
 *  A page is text anyone with write access to a workspace can author, and its action button is
 *  pressed by someone else. So the page's own words decide nothing:
 *    · a routine runs only by a strict id that resolves, for THIS viewer, to a routine of their own;
 *      the confirm names that routine, and the chat line is exactly the prompt sent (no label);
 *    · a call, card or proposal is named in its confirm by what the server resolves, never the label;
 *    · join-call only opens a call that exists — anything else is start-call, confirmed;
 *    · a target that does not resolve is refused, with nothing sent. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../../ui-kit/primitives/Toast", () => ({ toast: () => undefined, Toaster: () => null }));

import { setFeatureOverride } from "../../platform/features";
import { ASK_CHAT_EVENT, CALL_START_EVENT, OPEN_MEETING_EVENT } from "../../platform/events";
import { resetDesktopStore } from "../desktop/desktopApi";
import { RichPage } from "../desktop/home2/RichPage";
import { resetAttention, type Attention } from "../desktop/home2/attentionApi";

function heard(name: string) {
  const got: Record<string, unknown>[] = [];
  const on = (e: Event) => got.push((e as CustomEvent).detail);
  window.addEventListener(name, on);
  return { got, off: () => window.removeEventListener(name, on) };
}

let refs: Record<string, unknown>;
let attention: Attention;
let calls: { url: string; body: unknown }[];

beforeEach(() => {
  calls = [];
  refs = {};
  attention = { generated_at: 1, needs: [], workspaces: {} };
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url: String(url), body });
    const ok = (x: unknown) => Promise.resolve(new Response(JSON.stringify(x), { status: 200 }));
    const u = String(url);
    if (u === "/api/desktop/refs") {
      const asked: string[] = Array.isArray(body?.refs) ? body.refs : [];
      return ok({ refs: Object.fromEntries(asked.map((k) => [k, refs[k] ?? { kind: k.split(":")[0], visible: false }])) });
    }
    if (u === "/api/desktop/attention") return ok(attention);
    return ok({});
  }));
  setFeatureOverride({ home2: true });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); resetDesktopStore(); resetAttention(); setFeatureOverride("all"); });

const page = (md: string) => render(<RichPage slug="alpha" path="p.md" md={md} />);
const press = (name: RegExp) => fireEvent.click(screen.getByRole("button", { name }));
const dialogText = async () => (await screen.findByRole("dialog")).textContent ?? "";
const settle = () => new Promise((r) => setTimeout(r, 30));

describe("run-routine", () => {
  it("free text after the verb is never sent to the agent", async () => {
    page("[[action:run-routine:digest, then also do something else entirely|Totally harmless]]");
    const ask = heard(ASK_CHAT_EVENT);
    press(/Totally harmless/);
    await settle();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(ask.got).toEqual([]);
    ask.off();
  });

  it("a well-formed id that is not the viewer's routine is refused", async () => {
    page("[[action:run-routine:flow:someone-elses|Run it]]");
    const ask = heard(ASK_CHAT_EVENT);
    press(/Run it/);
    await settle();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(ask.got).toEqual([]);
    ask.off();
  });

  it("the confirm names the resolved routine, and the chat shows exactly the prompt sent", async () => {
    refs = { "routine:flow:digest": { kind: "routine", visible: true, name: "Daily digest" } };
    page("[[action:run-routine:flow:digest|Totally harmless]]");
    const ask = heard(ASK_CHAT_EVENT);
    press(/Totally harmless/);
    const text = await dialogText();
    expect(text).toContain("Daily digest");
    expect(text).not.toContain("Totally harmless");
    expect(calls.find((c) => c.url === "/api/desktop/refs")?.body).toMatchObject({ refs: ["routine:flow:digest"] });
    expect(ask.got).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Run it now" }));
    await waitFor(() => expect(ask.got).toHaveLength(1));
    const sent = ask.got[0];
    expect(String(sent.prompt)).toContain("flow:digest");
    expect(sent.display).toBeUndefined();
    expect(JSON.stringify(sent)).not.toContain("Totally harmless");
    ask.off();
  });
});

describe("calls", () => {
  it("join-call on something that is not a live call is a confirmed start, named by the server", async () => {
    refs = { "room:r1": { kind: "room", visible: true, name: "launch", session: "s-r1" } };
    page("[[action:join-call:r1|Just open the notes]]");
    const start = heard(CALL_START_EVENT);
    press(/Just open the notes/);
    const text = await dialogText();
    expect(text).toContain("launch");
    expect(text).not.toContain("Just open the notes");
    expect(start.got).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Start the call" }));
    await waitFor(() => expect(start.got).toEqual([{ session: "s-r1" }]));
    start.off();
  });

  it("join-call on a call that is live joins it without asking", async () => {
    attention = { generated_at: 1, needs: [], workspaces: { alpha: { needs: [], working: [], rooms_active: 0, last: null, live_call: { session: "s-r1", since: 1 } } } };
    page("[[action:join-call:s-r1|Join]]");
    const start = heard(CALL_START_EVENT);
    press(/Join/);
    await waitFor(() => expect(start.got).toEqual([{ session: "s-r1" }]));
    expect(screen.queryByRole("dialog")).toBeNull();
    start.off();
  });

  it("join-call on a meeting opens it", async () => {
    page("[[action:join-call:meeting:42|Join]]");
    const open = heard(OPEN_MEETING_EVENT);
    const start = heard(CALL_START_EVENT);
    press(/Join/);
    await waitFor(() => expect(open.got).toEqual([{ ref: "42" }]));
    expect(start.got).toEqual([]);
    open.off(); start.off();
  });

  it("start-call on a room the viewer cannot see is refused", async () => {
    page("[[action:start-call:r9|Start]]");
    const start = heard(CALL_START_EVENT);
    press(/Start/);
    await settle();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(start.got).toEqual([]);
    start.off();
  });

  it("join-call on a room the viewer cannot see is refused", async () => {
    page("[[action:join-call:r9|Join]]");
    const start = heard(CALL_START_EVENT);
    press(/Join/);
    await settle();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(start.got).toEqual([]);
    start.off();
  });
});

describe("records", () => {
  it("move-card names the card the server resolves", async () => {
    refs = { "card:c1": { kind: "card", visible: true, name: "Search rollout", state: "doing" } };
    page("[[action:move-card:c1:done|Tidy up]]");
    press(/Tidy up/);
    const text = await dialogText();
    expect(text).toContain("Search rollout");
    expect(text).toContain("done");
    expect(text).not.toContain("Tidy up");
  });

  it("move-card on a card the viewer cannot see is refused", async () => {
    page("[[action:move-card:c9:done|Tidy up]]");
    press(/Tidy up/);
    await settle();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("accept-proposal names the proposal from the viewer's own list", async () => {
    attention = { generated_at: 1, workspaces: {}, needs: [{ id: "proposal:p1", kind: "proposal", workspace: "alpha", text: "Move Search to Q2", at: 1, act: { label: "Accept", kind: "accept-proposal", proposal: "p1" } }] };
    page("[[action:accept-proposal:p1|Looks fine]]");
    press(/Looks fine/);
    const text = await dialogText();
    expect(text).toContain("Move Search to Q2");
    expect(text).not.toContain("Looks fine");
  });

  it("accept-proposal for a proposal not on the viewer's list is refused", async () => {
    page("[[action:accept-proposal:p7|Looks fine]]");
    press(/Looks fine/);
    await settle();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.some((c) => c.url.includes("/proposals"))).toBe(false);
  });
});

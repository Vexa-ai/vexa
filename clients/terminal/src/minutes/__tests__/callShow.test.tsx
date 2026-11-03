/** SHOW TO EVERYONE IN A CALL (founder 2026-10-15): a page opens for everyone, follows the presenter,
 *  highlights; a page someone cannot read is refused and offered to be shared first. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { OPEN_PAGE_EVENT } from "../../platform/events";
import { applyHighlight, callShowState, receiveShow, setShowTransport, shareAndShow, showPage, stopFollowing, stopShowing, follow } from "../callShow";
import { CallShowBar } from "../CallShowBar";

let sent: unknown[] = [];
function page(): HTMLElement {
  document.body.innerHTML = `<div data-pane="pages"><div data-doc-body style="overflow-y:auto">
    <h1>Sponsor plan</h1><h2>MongoDB</h2><p>Keen to sponsor the November meetup.</p></div></div>`;
  const body = document.querySelector<HTMLElement>("[data-doc-body]")!;
  Object.defineProperty(body, "scrollHeight", { value: 1100, configurable: true });
  Object.defineProperty(body, "clientHeight", { value: 100, configurable: true });
  return body;
}

beforeEach(() => { sent = []; setShowTransport({ send: (e) => sent.push(e), me: "2", nameOf: (id) => (id === "60" ? "Valeriia" : id) }); });
afterEach(() => { cleanup(); setShowTransport(null); vi.unstubAllGlobals(); document.body.innerHTML = ""; });

describe("a page shown by someone else", () => {
  it("opens in my panel, titled by who shows it, and I follow their scroll and highlights", () => {
    const body = page();
    const opened: unknown[] = [];
    const on = (e: Event) => opened.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_PAGE_EVENT, on);
    receiveShow({ type: "show", by: "60", workspace: "ait", path: "sponsor-plan.md", title: "Sponsor plan" });
    window.removeEventListener(OPEN_PAGE_EVENT, on);
    expect(opened).toEqual([{ workspace: "ait", path: "sponsor-plan.md" }]);
    expect(callShowState()).toMatchObject({ presenter: false, following: true, shown: { byName: "Valeriia", title: "Sponsor plan" } });
    receiveShow({ type: "follow", by: "60", ratio: 0.5 });
    expect(body.scrollTop).toBe(500);
    receiveShow({ type: "highlight", by: "60", anchor: "keen to sponsor" });
    expect(document.querySelector("[data-show-highlight]")?.textContent).toContain("Keen to sponsor");
  });

  it("stop following: I browse on my own until I follow again; messages from anyone else are ignored", () => {
    const body = page();
    receiveShow({ type: "show", by: "60", workspace: "ait", path: "sponsor-plan.md" });
    stopFollowing();
    receiveShow({ type: "follow", by: "60", ratio: 1 });
    expect(body.scrollTop).toBe(0);
    follow();
    receiveShow({ type: "follow", by: "77", ratio: 1 });
    expect(body.scrollTop).toBe(0);
    receiveShow({ type: "follow", by: "60", ratio: 1 });
    expect(body.scrollTop).toBe(1000);
  });

  it("the bar says who shows what, and offers Stop following / Follow", () => {
    page();
    receiveShow({ type: "show", by: "60", workspace: "ait", path: "sponsor-plan.md", title: "Sponsor plan" });
    render(<CallShowBar />);
    expect(screen.getByText(/Shown by Valeriia/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stop following" }));
    expect(screen.getByRole("button", { name: "Follow Valeriia" })).toBeTruthy();
  });
});

describe("showing a page myself", () => {
  it("as the presenter my selection goes to the room as a highlight, and Stop showing ends it for all", async () => {
    page();
    receiveShow({ type: "show", by: "2", workspace: "ait", path: "sponsor-plan.md" });
    expect(callShowState().presenter).toBe(true);
    stopShowing();
    expect(sent.at(-1)).toEqual({ type: "stop", by: "2" });
    expect(callShowState().shown).toBeNull();
  });

  it("a heading or a quote is found, highlighted and scrolled to", () => {
    page();
    expect(applyHighlight("MongoDB")).toBe(true);
    expect(applyHighlight("not on the page")).toBe(false);
  });

  it("a page someone cannot read is refused, never shown; the offer shares the workspace first, then shows it", async () => {
    const calls: { url: string; body?: unknown }[] = [];
    let refuse = true;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (String(url).endsWith("/call/show") && refuse) {
        refuse = false;
        return new Response(JSON.stringify({ detail: { error: "not_shared", workspace: "ait", message: "Share it first",
          missing: [{ subject: "60", name: "Valeriia" }] } }), { status: 409, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    await showPage("pchat-1", { workspace: "ait", path: "plan.md" });
    expect(callShowState().offer?.missing).toEqual([{ subject: "60", name: "Valeriia" }]);
    expect(callShowState().shown).toBeNull();
    await shareAndShow();
    expect(calls.map((c) => c.url)).toEqual(["/api/sessions/pchat-1/call/show", "/api/workspace/members/add", "/api/sessions/pchat-1/call/show"]);
    expect(calls[1].body).toEqual({ workspace: "ait", person: "60", role: "contributor" });
    expect(callShowState().offer).toBeNull();
  });
});

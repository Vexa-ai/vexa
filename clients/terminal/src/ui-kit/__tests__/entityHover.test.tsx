/** EVERY ENTITY CHIP: SMALL ON HOVER, CLICK OPENS THE PAGE (founder 2026-10-15, on a page table: "this
 *  takes too much space — make it small and only on hover, click should open page"; "so this should
 *  be some things on hover"). The card is the name and at most three short lines — role ·
 *  organisation, the most recent touch, the first sentence — never TYPE/ID/TITLE/ALIASES, never a
 *  button; it comes after a short delay, goes on mouse-out, and never covers the chip's own line. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WsLink } from "../WsLink";
import { invalidateWsLinkCaches } from "../wsLinks";
import { hoverFacts, shortDate } from "../hoverFacts";
import { HOVER_DELAY_MS, resetHoverCache } from "../hoverCard";
import { OPEN_ENTITY_EVENT } from "../../platform";

const NOW = Date.UTC(2026, 9, 15);
const ANNA = `---
type: person
id: anna-barinova
title: Anna Barinova
aliases: [Anna B]
role: Head of Partnerships
company: "[[Acme Bank]]"
last_met: 2026-10-07
last_meeting: AI Tinkerers call
---
# Anna Barinova

Runs partnerships at Acme Bank and organises the Lisbon AI meetups. Met at Web Summit.
`;

describe("what the card says", () => {
  it("role · organisation, the most recent touch, the first sentence — nothing from the property table", () => {
    expect(hoverFacts(ANNA, "person", NOW)).toEqual([
      "Head of Partnerships · Acme Bank",
      "Last met Oct 7 · AI Tinkerers call",
      "Runs partnerships at Acme Bank and organises the Lisbon AI meetups.",
    ]);
  });
  it("a line with no data is left out; a touch is read from a dated interactions list; a room is how you are connected", () => {
    const md = "---\ntype: person\n---\n# Bo\n\n## Interactions\n- 2026-09-02 email about pricing\n- 2026-10-01 — Demo call with the team\n";
    expect(hoverFacts(md, "person", NOW)).toEqual(["Last met Oct 1 · Demo call with the team"]);
    expect(hoverFacts("---\ntype: person\nvia: Collab Room\n---\n", "person", NOW)).toEqual(["In Collab Room"]);
    expect(hoverFacts("", "person", NOW)).toEqual([]);
  });
  it("a company is its industry · place", () => {
    expect(hoverFacts("---\ntype: company\nindustry: Banking\nlocation: Lisbon\n---\n", "company", NOW)[0]).toBe("Banking · Lisbon");
  });
  it("dates are short, with the year only when it is not this one", () => {
    expect(shortDate("2026-10-07", NOW)).toBe("Oct 7");
    expect(shortDate("2025-03-02", NOW)).toBe("Mar 2, 2025");
  });
});

describe("the chip", () => {
  beforeEach(() => {
    invalidateWsLinkCaches(); resetHoverCache();
    globalThis.fetch = vi.fn(async (u: string) => ({ ok: true, status: 200, json: async () => (String(u).includes("/api/workspace/file")
      ? { content: ANNA }
      : { results: [{ ref: "ws:k4m5x2q7bd/anna", title: "Anna Barinova", url: "/w/k4m5x2q7bd/kg/entities/person/anna-barinova.md",
          access: "readable", path: "kg/entities/person/anna-barinova.md", slug: "grp", workspace: "Biz", type: "person" }] }) }) as unknown as Response) as unknown as typeof fetch;
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

  it("hover shows the small card after a delay and mouse-out hides it; the card has no buttons", async () => {
    render(<WsLink refText="ws:k4m5x2q7bd/anna" />);
    const chip = await screen.findByRole("link");
    vi.useFakeTimers();
    fireEvent.mouseEnter(chip);
    expect(document.querySelector("[data-hover-card]")).toBeNull();             // not at once
    await act(async () => { vi.advanceTimersByTime(HOVER_DELAY_MS + 10); });
    vi.useRealTimers();
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    const card = document.querySelector("[data-hover-card]")!;
    expect(card.textContent).toContain("Anna Barinova");
    expect(card.textContent).toContain("Runs partnerships at Acme Bank and organises the Lisbon AI meetups.");   // the story
    expect(card.textContent).not.toMatch(/TYPE|ALIASES|anna-barinova/i);
    expect(card.querySelectorAll("button, a")).toHaveLength(0);
    expect(card.querySelectorAll("[data-hover-line]").length).toBeLessThanOrEqual(3);
    fireEvent.mouseLeave(chip);
    expect(document.querySelector("[data-hover-card]")).toBeNull();
  });

  it("a click opens the page directly — no card in place", async () => {
    const opened: unknown[] = [];
    const on = (e: Event) => opened.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_ENTITY_EVENT, on);
    render(<WsLink refText="ws:k4m5x2q7bd/anna" />);
    fireEvent.click(await screen.findByRole("link"));
    window.removeEventListener(OPEN_ENTITY_EVENT, on);
    expect(opened).toEqual([expect.objectContaining({ path: "kg/entities/person/anna-barinova.md", slug: "grp" })]);
    expect(document.querySelector("[data-peek], [data-page-peek]")).toBeNull();
  });

  it("keyboard: focus shows the card, Enter opens, Esc hides", async () => {
    const opened: unknown[] = [];
    const on = (e: Event) => opened.push(e);
    window.addEventListener(OPEN_ENTITY_EVENT, on);
    render(<WsLink refText="ws:k4m5x2q7bd/anna" />);
    const chip = await screen.findByRole("link");
    vi.useFakeTimers();
    fireEvent.focus(chip);
    await act(async () => { vi.advanceTimersByTime(HOVER_DELAY_MS + 10); });
    vi.useRealTimers();
    expect(document.querySelector("[data-hover-card]")).not.toBeNull();
    fireEvent.keyDown(chip, { key: "Escape" });
    expect(document.querySelector("[data-hover-card]")).toBeNull();
    fireEvent.keyDown(chip, { key: "Enter" });
    window.removeEventListener(OPEN_ENTITY_EVENT, on);
    expect(opened).toHaveLength(1);
  });

  it("sits below the chip's line, never over it", async () => {
    render(<WsLink refText="ws:k4m5x2q7bd/anna" />);
    const chip = await screen.findByRole("link");
    chip.getBoundingClientRect = () => ({ top: 100, bottom: 120, left: 40, right: 140, width: 100, height: 20, x: 40, y: 100, toJSON() {} }) as DOMRect;
    vi.useFakeTimers();
    fireEvent.mouseEnter(chip);
    await act(async () => { vi.advanceTimersByTime(HOVER_DELAY_MS + 10); });
    vi.useRealTimers();
    const card = document.querySelector("[data-hover-card]") as HTMLElement;
    expect(parseFloat(card.style.top)).toBeGreaterThanOrEqual(120);
  });
});

describe("the summary line is prose or nothing (founder: raw relation bullets on a chip in a chat)", () => {
  it("skips lists, relation lines and anything with a date, slug or id; no prose → no line", async () => {
    const { proseSentence } = await import("../hoverFacts");
    const relations = "---\ntype: person\nrole: Engineer\n---\n# Imad\n\n- Imad's agent-built multiplayer game — building\n- 2026-10-15 how-iich-wse — AI creativity — meeting\n";
    expect(proseSentence(relations)).toBe("");
    expect(hoverFacts(relations, "person", NOW)).toEqual(["Engineer"]);
    expect(proseSentence("Imad's game — building — works on\n\nImad builds games with agents. He lives in Porto.")).toBe("Imad builds games with agents.");
    expect(proseSentence("Met on 2026-10-15 in how-iich-wse.")).toBe("");
  });
});

describe("the short story (founder: \"this should tell the full story, short one\")", () => {
  it("the page's summary first, else up to three prose sentences — wrapped, ending on a sentence", async () => {
    const { hoverStory, STORY_MAX } = await import("../hoverFacts");
    const joe = `---\ntype: person\nsummary: "Founder of AI Tinkerers HQ; approves chapter budgets. You run the Lisbon chapter with him. Waiting on his answer about the invoice customer since Oct 13."\n---\n# Joe\n\n- 2026-10-15 how-iich-wse — meeting\n`;
    expect(hoverStory(joe)).toBe("Founder of AI Tinkerers HQ; approves chapter budgets. You run the Lisbon chapter with him. Waiting on his answer about the invoice customer since Oct 13.");
    const prose = "---\ntype: person\n---\n# Ana\n\nAna runs Unicorn Factory events. She hosted the September meetup. She wants 80 seats confirmed. She also likes coffee.";
    const s = hoverStory(prose);
    expect(s).toBe("Ana runs Unicorn Factory events. She hosted the September meetup. She wants 80 seats confirmed.");
    expect(s.length).toBeLessThanOrEqual(STORY_MAX);
    expect(hoverStory("---\ntype: person\n---\n- 2026-10-15 x — meeting\n")).toBe("");
  });
});

describe("a chip is one readable page, or plain text (founder, live: a \"Qonto 3\" picker of addresses)", () => {
  const answer = (body: unknown) => {
    invalidateWsLinkCaches(); resetHoverCache();
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => body }) as unknown as Response) as unknown as typeof fetch;
  };
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });
  it("a page that does not exist is plain text — no chip, no picker", async () => {
    answer({ results: [{ ref: "ws:k4m5x2q7bd/qonto", title: "Qonto", access: "readable", missing: true, url: null }] });
    const { container } = render(<WsLink refText="ws:k4m5x2q7bd/qonto" />);
    await screen.findByText("Qonto");
    expect(container.querySelector(".vx-chip")).toBeNull();
    expect(container.querySelector("[data-ws-missing]")).not.toBeNull();
  });
  it("the page in this workspace wins; a real picker has no count badge and no addresses", async () => {
    const cands = [{ title: "Qonto", path: "kg/qonto.md", slug: "grp", workspace: "AI Tinkerers Lisbon" },
                   { title: "Qonto", path: "kg/q.md", slug: "biz", workspace: "biz" },
                   { title: "Qonto", path: "kg/q.md", slug: "127", workspace: "test@vexa.ai" }];
    answer({ results: [{ ref: "ws:k4m5x2q7bd/qonto", title: "Qonto", access: "readable", ambiguous: true, candidates: cands }] });
    const here = render(<WsLink refText="ws:k4m5x2q7bd/qonto" slug="grp" />);
    expect((await screen.findByRole("link")).textContent).toBe("Qonto");
    here.unmount();
    answer({ results: [{ ref: "ws:k4m5x2q7bd/qonto", title: "Qonto", access: "readable", ambiguous: true, candidates: cands }] });
    const { container } = render(<WsLink refText="ws:k4m5x2q7bd/qonto" slug="other" />);
    const chip = await screen.findByRole("button");
    expect(chip.textContent).toBe("Qonto");                                   // no "3"
    fireEvent.click(chip);
    const menu = document.body.textContent ?? "";
    expect(menu).toContain("AI Tinkerers Lisbon");
    expect(menu).not.toContain("@");
    void container;
  });
});

/** THE NEW-CHAT HOME AND THE ONE BLOCKS RENDERER (#1783).
 *
 *  Pinned here: every moment of the approved mockup is expressible in blocks.v1 and draws; malformed
 *  specs never break the page; the stream caps at five on a phone; dismiss asks why; every button goes
 *  to the host (a prompt, an open, a message) and nothing acts on its own; a person opens a peek with
 *  Message and Open; the 1:1 chat id is the same from both sides; external messages only draft; the
 *  dashboard patches a card in place (prepared → Continue, done → gone); the prep chip is not offered
 *  for a meeting whose prep chat exists; the same blocks draw in a chat reply's ```vexa fence; and
 *  progressive disclosure: collapse, show more, peek (Esc folds), expand in place, depth 3.
 *  The server half: `core/agent/tests/test_home.py`. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { HomeStream, UNDO_MS } from "../HomeStream";
import { directChatId, externalMessagePrompt, homePrompt } from "../homeApi";
import { proposals, type ConnectionRow } from "../proposals";
import { BlocksProvider, homeFirstVisit, LIMITS, patchHome, sanitizeHome, VexaFence, type BlocksHandlers, type HomeDoc } from "../../ui-kit";
import { Markdown } from "../../ui-kit/Markdown";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const GOLDEN = join(__dirname, "..", "..", "..", "..", "..", "core", "agent", "contracts");
const scene = (n: string) => JSON.parse(readFileSync(join(GOLDEN, "home-card.v1", "golden", `HomeDoc.${n}.json`), "utf8"));
const fence = () => readFileSync(join(GOLDEN, "blocks.v1", "golden", "Fence.page.json"), "utf8");

function handlers(): BlocksHandlers & { calls: unknown[][] } {
  const calls: unknown[][] = [];
  return {
    calls,
    onAction: (c, a) => calls.push(["action", c?.id ?? null, a.prompt ?? a.open]),
    onMessage: (p, c) => calls.push(["message", p.key, c?.id ?? null]),
    onOpenPerson: (p) => calls.push(["page", p.key]),
    onDismiss: (c, r) => calls.push(["dismiss", c.id, r]),
    onSnooze: (c) => calls.push(["snooze", c.id]),
    onExpand: async (c) => { calls.push(["expand", c.id]); return { blocks: [{ type: "text", text: "Three tiers, drafted six days ago." }] }; },
  };
}

function phone(on: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: on && q.includes("max-width"), addEventListener() {}, removeEventListener() {} }));
}

const draw = (doc: HomeDoc, h = handlers()) => render(<BlocksProvider value={h}><HomeStream doc={doc} /></BlocksProvider>).container;

describe("every moment of the mockup is expressible in blocks.v1", () => {
  for (const name of ["busy", "after", "quiet"]) {
    it(`draws the ${name} moment whole`, () => {
      phone(false);
      const raw = scene(name);
      const doc = sanitizeHome(raw) as HomeDoc;
      expect(doc.cards.map((c) => c.id)).toEqual(raw.cards.map((c: { id: string }) => c.id));
      const c = draw(doc);
      expect(c.querySelector(".vx-home-greet h2")?.textContent).toBe(raw.greeting.title);
      expect(c.querySelectorAll("[data-home-card]").length).toBeGreaterThanOrEqual(raw.cards.length);
      // the line is drawn in sentence case, whatever case the composition wrote it in
      expect(c.querySelector("[data-home-left-out]")).toBeNull();        // no "Not shown now" footer (founder, live home)
    });
  }

  it("carries the mockup's shapes: a wide card, a nested attach, a grouped list, evidence and a checklist", () => {
    phone(false);
    const busy = draw(sanitizeHome(scene("busy"))!);
    expect(busy.querySelector('[data-home-card="m-dev-checkin"]')?.className).toContain("wide");
    expect(busy.querySelector('[data-home-card="m-dev-checkin"] [data-home-card="s-commitments"]')).not.toBeNull();
    expect(busy.querySelectorAll('[data-home-card="w-replies"] .vx-blk-row').length).toBe(2);
    expect(busy.querySelector(".vx-blk-ev")).not.toBeNull();
    cleanup();
    expect(draw(sanitizeHome(scene("after"))!).querySelectorAll(".vx-blk-prop").length).toBe(4);
  });
});

describe("a malformed composition never breaks the page", () => {
  it("survives fuzzed specs", () => {
    phone(false);
    let seed = 11;
    const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
    const junk: unknown[] = [null, 0, -1, "", "x".repeat(4000), [], {}, true, { type: "attach" }, { type: "card" }, "<script>alert(1)</script>"];
    const mutate = (v: unknown, d = 0): unknown => {
      if (d > 6 || rnd() < 0.15) return junk[Math.floor(rnd() * junk.length)];
      if (Array.isArray(v)) return v.map((x) => mutate(x, d + 1));
      if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).filter(() => rnd() > 0.1).map(([k, x]) => [k, mutate(x, d + 1)]));
      return v;
    };
    for (let i = 0; i < 300; i++) {
      const doc = sanitizeHome(mutate(scene("busy")));
      if (!doc) continue;
      const c = draw(doc);
      expect(c.querySelector("script")).toBeNull();   // text stays text, even in an attribute
      cleanup();
    }
    expect(sanitizeHome("nope")).toBeNull();
    expect(sanitizeHome({ spec: "home-card.v2", cards: [] })).toBeNull();
  });

  it("drops what it does not know and caps what it does", () => {
    const doc = sanitizeHome({ spec: "home-card.v1", greeting: { title: "Hi" }, people: {},
      cards: [{ type: "card", id: "a", kind: "evil", blocks: [{ type: "html", html: "<b>" }, { type: "title", text: "t".repeat(999) },
        { type: "action", label: "Go", href: "javascript:x" }, { type: "action", label: "Ask", prompt: "do it" }] }] })!;
    expect(doc.cards[0].kind).toBe("suggestion");
    expect(doc.cards[0].blocks.map((b) => b.type)).toEqual(["title", "action"]);
    expect((doc.cards[0].blocks[0] as { text: string }).text.length).toBe(LIMITS.text);
  });
});

describe("the stream", () => {
  it("is one column of at most five at phone width, with no footer about what it cut", () => {
    phone(true);
    const c = draw(sanitizeHome(scene("quiet"))!);
    expect(c.querySelector(".vx-home-stream")?.className).toContain("one");
    expect(c.querySelectorAll(".vx-home-stream > [data-home-card]").length).toBe(5);
    expect(c.querySelector("[data-home-left-out]")).toBeNull();
  });

  it("asks why before a dismiss, and a snooze needs no reason — each can be undone before it is sent", () => {
    vi.useFakeTimers();
    try {
      phone(false);
      const h = handlers();
      const c = draw(sanitizeHome(scene("quiet"))!, h);
      const card = c.querySelector('[data-home-card="s-lena-retest"]')!;
      fireEvent.click(card.querySelector('[aria-label="Dismiss"]')!);
      fireEvent.click(card.querySelector('[data-reason="wrong"]')!);
      expect(c.querySelector('[data-home-card="s-lena-retest"]')).toBeNull();      // gone at once
      expect(c.querySelector("[data-home-toast]")?.textContent).toContain("Undo");
      expect(h.calls).toEqual([]);                                                  // not told yet
      act(() => { vi.advanceTimersByTime(UNDO_MS); });
      expect(h.calls).toContainEqual(["dismiss", "s-lena-retest", "wrong"]);
      fireEvent.click(c.querySelector('[data-home-card="t-routine"] [aria-label="Snooze for a day"]')!);
      fireEvent.click(c.querySelector("[data-home-undo]")!);                        // taken back
      expect(c.querySelector('[data-home-card="t-routine"]')).not.toBeNull();
      act(() => { vi.advanceTimersByTime(UNDO_MS); });
      expect(h.calls).not.toContainEqual(["snooze", "t-routine"]);
      fireEvent.click(c.querySelector('[data-home-card="t-routine"] [aria-label="Snooze for a day"]')!);
      act(() => { vi.advanceTimersByTime(UNDO_MS); });
      expect(h.calls).toContainEqual(["snooze", "t-routine"]);
    } finally { vi.useRealTimers(); }
  });

  it("every button hands its prompt, open or person to the host — nothing acts on its own", () => {
    phone(false);
    const h = handlers();
    const c = draw(sanitizeHome(scene("after"))!, h);
    fireEvent.click(c.querySelector('[data-home-card="m-product-sync"] [data-home-action="Prepare"]')!);
    fireEvent.click(c.querySelector('[data-home-card="c-pilot-scope"] [data-home-action="Continue"]')!);
    fireEvent.click(c.querySelector('[data-home-card="s-hand-mira"] [data-home-action="Message Mira"]')!);
    expect(h.calls).toEqual([
      ["action", "m-product-sync", "Prepare me for the 15:00 product sync with Jonas Weber."],
      ["action", "c-pilot-scope", { chat: "chat-pilot-scope" }],
      ["message", "mira", "s-hand-mira"],
    ]);
  });

  it("a person chip shows a small hover card and a click opens their page; an external send is gated", async () => {
    phone(false);
    vi.useFakeTimers();
    try {
      const h = handlers();
      const c = draw(sanitizeHome(scene("quiet"))!, h);
      const chip = c.querySelector('[data-home-card="s-paul-model"] [data-person="paul"]')!;
      fireEvent.mouseEnter(chip);
      act(() => { vi.advanceTimersByTime(400); });
      const card = document.querySelector("[data-hover-card]")!;
      expect(card.textContent).toContain("Paul");
      expect(card.querySelectorAll("button")).toHaveLength(0);       // no Open, no ×, no Message
      fireEvent.mouseLeave(chip);
      expect(document.querySelector("[data-hover-card]")).toBeNull();
      fireEvent.click(chip);
      expect(h.calls).toContainEqual(["page", "paul"]);
      expect(c.querySelector('[data-home-card="s-paul-model"] .vx-blk-btn.gate')).not.toBeNull();
    } finally { vi.useRealTimers(); }
  });

});

describe("a live dashboard of ongoing work", () => {
  const meeting = () => sanitizeHome({ spec: "home-card.v1", greeting: { title: "Hi" }, people: {}, cards: [
    { type: "card", id: "m-41", kind: "meeting", blocks: [{ type: "title", text: "Funding model call" },
      { type: "action", label: "Prepare", primary: true, prompt: "Prepare me for the funding model call." },
      { type: "action", label: "Send bot", prompt: "Send the bot." }] },
    { type: "card", id: "w-1", kind: "waiting", blocks: [{ type: "row", text: "Reply to Paul" }] }] })!;

  it("a prepared meeting continues its prep chat instead of preparing again, in place", () => {
    phone(false);
    const h = handlers();
    const doc = patchHome(meeting(), { item: "m-41", state: "prepared", chat: "meet-41" }, Date.now());
    expect(doc.cards.map((c) => c.id)).toEqual(["m-41", "w-1"]);          // no reorder
    const c = draw(doc, h);
    const card = c.querySelector('[data-home-card="m-41"]')!;
    expect(card.querySelector('[data-home-action="Prepare"]')).toBeNull();
    expect(card.querySelector('[data-state="prepared"]')?.textContent).toBe("Prepared");
    expect(card.textContent).toContain("updated just now");
    fireEvent.click(card.querySelector('[data-home-action="Continue"]')!);
    expect(h.calls).toEqual([["action", "m-41", { chat: "meet-41" }]]);
  });

  it("a reply sent or work done takes the card off", () => {
    expect(patchHome(meeting(), { item: "w-1", state: "replied" }, 1).cards.map((c) => c.id)).toEqual(["m-41"]);
    expect(patchHome(meeting(), { item: "m-41", state: "nonsense" }, 1)).toEqual(meeting());
  });

  it("the prompt a card sends names its item, so the work can report where it got to", () => {
    expect(homePrompt("Prepare me.", "m-41")).toMatch(/item m-41.*home_item_update/s);
  });

  it("the standing prep chip is not offered for a meeting whose prep chat exists", () => {
    const rows: ConnectionRow[] = [{ id: "c", provider: "google_calendar", status: "ready" }];
    const now = Date.parse("2026-10-13T08:00:00Z");
    const ev = [{ title: "Funding model call", start: "2026-10-13T10:00:00Z" }];
    expect(proposals(rows, ev, now).some((p) => p.kind === "prep")).toBe(true);
    expect(proposals(rows, ev, now, null, null, [], ["Prep: Funding model call"]).some((p) => p.kind === "prep")).toBe(false);
  });
});

describe("one renderer, every surface: progressive disclosure", () => {
  it("a chat reply's ```vexa fence draws as blocks, and broken JSON stays readable code", () => {
    const md = "Here is the state:\n\n```vexa\n" + fence() + "\n```\n";
    const c = render(<Markdown>{md}</Markdown>).container;
    expect(c.querySelector("[data-vexa-fence]")).not.toBeNull();
    expect(c.querySelector(".vx-blk-title")?.textContent).toBe("Pilot: open items");
    cleanup();
    const bad = render(<VexaFence source="{not json" />).container;
    expect(bad.querySelector("pre")?.textContent).toBe("{not json");
  });

  it("a list shows three rows, then the rest in place; a collapsed group opens in place", () => {
    const c = render(<VexaFence source={fence()} />).container;
    expect(c.querySelectorAll(".vx-blk-list .vx-blk-row").length).toBe(LIMITS.showRows);
    fireEvent.click(c.querySelector("[data-show-more]")!);
    expect(c.querySelectorAll(".vx-blk-list .vx-blk-row").length).toBe(4);
    const head = c.querySelector(".vx-blk-grouphead")!;
    expect(head.getAttribute("aria-expanded")).toBe("false");
    expect(c.querySelector(".vx-blk-ev")).toBeNull();
    fireEvent.click(head);
    expect(c.querySelector(".vx-blk-ev")).not.toBeNull();
  });

  it("a card folds to one line and opens again; Expand inserts the agent's blocks under it", async () => {
    phone(false);
    const h = handlers();
    const c = draw(sanitizeHome(scene("quiet"))!, h);
    const card = c.querySelector('[data-home-card="s-paul-model"]')!;
    fireEvent.click(card.querySelector('[aria-label="Collapse section"]')!);
    expect(card.querySelector("[data-folded]")?.textContent).toBe("Send Paul Okafor the draft funding model");
    fireEvent.click(card.querySelector("[data-folded]")!);
    await act(async () => { fireEvent.click(card.querySelector('[aria-label="Expand: ask the agent to elaborate"]')!); });
    expect(h.calls).toContainEqual(["expand", "s-paul-model"]);
    expect(card.querySelector("[data-expanded]")?.textContent).toContain("Three tiers");
  });

  it("nesting stops at three", () => {
    let raw: Record<string, unknown> = { type: "card", id: "d0", kind: "suggestion", blocks: [{ type: "title", text: "deepest" }] };
    for (let i = 1; i < 6; i++) raw = { type: "card", id: `d${i}`, kind: "suggestion", blocks: [{ type: "title", text: `level ${i}` }, { type: "attach", card: raw }] };
    const doc = sanitizeHome({ spec: "home-card.v1", greeting: { title: "x" }, people: {}, cards: [raw] })!;
    expect(draw(doc).querySelectorAll("[data-home-card]").length).toBeLessThanOrEqual(LIMITS.depth + 1);
  });
});

describe("messaging a person", () => {
  it("names one 1:1 chat for the two of them, from either side", () => {
    expect(directChatId("u_a", "u_b")).toBe(directChatId("u_b", "u_a"));
    expect(directChatId("u_a", "u_b")).not.toBe(directChatId("u_a", "u_c"));
    expect(directChatId("u_a", "u_b")).toMatch(/^dm-[0-9a-f]{16}$/);
  });

  it("drafts to an external person and never sends without the explicit go", () => {
    const text = externalMessagePrompt({ key: "p", name: "Paul Okafor", initials: "PO", vexa: false, email: "paul@example.org", reach: "Email" });
    expect(text).toContain("Paul Okafor <paul@example.org>");
    expect(text).toMatch(/do not send anything until I give my explicit go/);
  });

  it("the first visit opens on the home, not on a greeting turn", () => {
    expect(homeFirstVisit({ kind: "first-visit" })).toBe(true);
    expect(homeFirstVisit({ kind: "prep" })).toBe(false);
    const shell = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");
    expect(shell).toMatch(/kick: homeFirstVisit\(sc\) \? undefined : sc\.openingText/);
    expect(shell).toMatch(/<HomeStream doc=\{homeRead\.doc\}/);
  });

  it("a picture is the server's media route, and the initials when it fails", () => {
    const doc = sanitizeHome({ spec: "home-card.v1", greeting: { title: "x" }, cards: [{ type: "card", id: "a", kind: "waiting",
      blocks: [{ type: "person", people: ["m"] }] }],
      people: { m: { name: "Mira Chen", vexa: true, avatar: "g:0123456789abcdef0123456789abcdef", presence: "now" },
                bad: { name: "X", vexa: false, avatar: "https://evil.example/x.png" } } })!;
    expect(doc.people.bad.avatar).toBeUndefined();
    const c = draw(doc);
    const img = c.querySelector("img.vx-blk-av") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/home/media/g%3A0123456789abcdef0123456789abcdef");
    fireEvent.error(img);
    expect(c.querySelector("img.vx-blk-av")).toBeNull();
    expect(c.querySelector("span.vx-blk-av")?.textContent).toBe("MC");
    expect(c.querySelector(".vx-blk-vx.now")).not.toBeNull();
  });
});

describe("the rich-pages seams draw the shared renderer", () => {
  it("doc.block draws a ```vexa fence in a page, and doc.peek opens a page peek with Open and Esc", async () => {
    await import("../register");
    const { registry } = await import("../../contributions");
    const Block = registry.tabComponent("doc.block")!;
    const c = render(<Block id="doc.block" active params={{ lang: "vexa", source: fence() }} />).container;
    expect(c.querySelector("[data-vexa-fence] .vx-blk-title")?.textContent).toBe("Pilot: open items");
    cleanup();
    const Peek = registry.tabComponent("doc.peek")!;
    const opened: string[] = [];
    const h: BlocksHandlers = { onPeekPage: async (path, slug) => ({ summary: `about ${slug}/${path}`, properties: [["Stage", "pilot"]] }) };
    const p = render(<BlocksProvider value={h}><Peek id="doc.peek:x" active
      params={{ path: "kg/entities/company/northwind.md", slug: "acme", title: "Northwind", onOpen: () => opened.push("open"), onClose: () => opened.push("close") }} /></BlocksProvider>).container;
    await act(async () => {});
    expect(p.querySelector("[data-page-peek]")?.textContent).toContain("about acme/kg/entities/company/northwind.md");
    expect(p.textContent).toContain("pilot");
    fireEvent.keyDown(p.querySelector("[data-page-peek]")!, { key: "Escape" });
    fireEvent.click(p.querySelector("[data-peek-open]")!);
    expect(opened).toEqual(["close", "close", "open"]);
  });
});

describe("a calm stream (founder, UI lab 2026-10-14)", () => {
  const css = (f: string) => readFileSync(join(__dirname, "..", "..", f), "utf8");
  it("draws at most one primary button a card; the rest are quiet", () => {
    const doc = sanitizeHome({ spec: "home-card.v1", greeting: { title: "Hi" }, people: {}, cards: [{ type: "card", id: "x", kind: "meeting",
      blocks: [{ type: "title", text: "Planning call" },
        { type: "action", label: "Prepare", prompt: "Prepare.", primary: true },
        { type: "action", label: "Send bot", prompt: "Send.", primary: true },
        { type: "row", text: "Agenda", action: { type: "action", label: "Open", prompt: "Open.", primary: true } }] }] }) as HomeDoc;
    const c = draw(doc);
    expect([...c.querySelectorAll(".vx-blk-btn.primary")].map((b) => b.textContent)).toEqual(["Prepare"]);
  });
  it("a card's controls are hover-only, its kind label is sentence case, and the stream leaves no empty cell", () => {
    const blocks = css("ui-kit/blocks/blocks.css");
    expect(blocks).toMatch(/\.vx-blk-ctl \{[^}]*opacity: 0/);
    expect(blocks).toMatch(/\.vx-blk-card:hover > \.vx-blk-ih > \.vx-blk-ctl/);
    expect(blocks).not.toMatch(/\.vx-blk-kl \{[^}]*(uppercase|font-mono)/);
    expect(css("minutes/home.css")).toMatch(/\.vx-home-stream > \.vx-blk-card \{ flex: 1 1/);
    expect(css("minutes/home.css")).not.toMatch(/\.vx-home-left \{[^}]*font-mono/);
  });
  it("a squeezed rail draws nothing instead of clipped counts", () => {
    expect(css("ui-kit/layout/layout.css")).toMatch(/@container \(max-width: 159px\) \{ \.vx-rail > \* \{ visibility: hidden; \} \}/);
  });
});

describe("home cards off for the release (founder 2026-10-15: \"let's just disable these cards\")", () => {
  it("the greeting, the starters and the People strip — nothing card-like", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ people: [], count: 0 }))));
    const doc = sanitizeHome({ spec: "home-card.v1", greeting: { title: "Good morning, Dmitry", text: "Thu 15 Oct" }, people: {}, cards: [] }) as HomeDoc;
    const c = render(<BlocksProvider value={handlers()}><HomeStream doc={doc} starters={<span data-starter>Check my email</span>} people={false} /></BlocksProvider>).container;
    expect(c.querySelector(".vx-home-greet h2")?.textContent).toBe("Good morning, Dmitry");
    expect(c.querySelector("[data-starter]")).not.toBeNull();
    expect(c.querySelectorAll("[data-home-card], .vx-blk-card, [data-home-left-out]")).toHaveLength(0);
    expect(c.querySelector("[data-people-strip]")).toBeNull();
  });
});

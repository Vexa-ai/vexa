/** THE DESKTOP AS DRAWN (#1783, desktop ADR §2.3a, D1) — `composeDesktop(desk, sections)` is the pure
 *  read model the `/api/desktop` route answers: the person's arrangement from crm-api, the tiles from
 *  agent-api, nothing stored. Pinned here: the default order, the person's order winning, a section
 *  type the layout does not know yet appended at the END, tiles ordered by the layout, a placement
 *  agent-api could not resolve drawn as `missing`, cards empty and hidden in D1, expired filters
 *  dropped. */
import { describe, expect, it } from "vitest";
import { composeDesktop, placedRefs, type CardProposal, type CardView, type Desk, type DesktopSections } from "../desktop";

const desk = (over: Partial<Desk> = {}): Desk => ({
  version: 3, undo_version: 2, saved: [], proposals: [], placements: [],
  layout: { sections: [], order: {}, filters: {} }, ...over,
});
const sections = (over: Partial<DesktopSections> = {}): DesktopSections => ({
  presence_visibility: "workspace_members", hidden_workspaces: 0,
  workspaces: [
    { slug: "alpha", id: "w1", name: "Alpha project", kind: "shared", access: "owner", write: true, members: [] },
    { slug: "beta", id: "w2", name: "Beta notes", kind: "private", access: "owner", write: true, members: [] },
  ],
  agents: [{ ref: "agent:morning", id: "morning", name: "Morning brief", status: "idle", missing: false }],
  files: [{ ref: "file:alpha/notes/plan.md", slug: "alpha", path: "notes/plan.md", title: "Plan", missing: false }],
  ...over,
});

describe("composeDesktop", () => {
  it("draws the default layout: Workspaces, Agents, Files, then Cards — hidden and empty in D1", () => {
    const d = composeDesktop(desk(), sections());
    expect(d.sections.map((s) => s.id)).toEqual(["workspaces", "agents", "files", "cards"]);
    expect(d.sections.map((s) => s.title)).toEqual(["Workspaces", "Agents", "Files", "Cards"]);
    expect(d.sections.slice(0, 3).every((s) => !s.hidden && !s.collapsed)).toBe(true);
    const cards = d.sections.find((s) => s.id === "cards")!;
    expect(cards.hidden).toBe(true);
    expect(cards.items).toEqual([]);
    expect(d.version).toBe(3);
    expect(d.undo_version).toBe(2);
  });

  it("keeps the person's section order, and a section the layout lacks is appended at the end", () => {
    const d = composeDesktop(desk({ layout: { sections: [
      { id: "files", hidden: false, collapsed: true }, { id: "workspaces" },
    ] } }), sections());
    expect(d.sections.map((s) => s.id)).toEqual(["files", "workspaces", "agents", "cards"]);
    expect(d.sections[0].collapsed).toBe(true);
  });

  it("drops a section id this client cannot draw, and never shows cards even when the layout says so", () => {
    const d = composeDesktop(desk({ layout: { sections: [
      { id: "cards", hidden: false }, { id: "group:later" }, { id: "agents", hidden: true },
    ] } }), sections());
    expect(d.sections.map((s) => s.id)).toEqual(["cards", "agents", "workspaces", "files"]);
    expect(d.sections[0].hidden).toBe(true);
    expect(d.sections[1].hidden).toBe(true);
  });

  it("orders workspaces by the layout, the rest after in the server's order", () => {
    const d = composeDesktop(desk({ layout: { order: { workspaces: ["workspace:beta", "workspace:gone"] } } }), sections());
    const ws = d.sections.find((s) => s.id === "workspaces")!.items;
    expect(ws.map((i) => i.ref)).toEqual(["workspace:beta", "workspace:alpha"]);
    expect(ws[0]).toMatchObject({ kind: "workspace", name: "Beta notes" });
  });

  it("draws one tile per placement, in the layout's order, and an unresolved one as missing", () => {
    const d = composeDesktop(desk({
      placements: [{ ref: "agent:morning" }, { ref: "agent:retired" }, { ref: "file:alpha/notes/plan.md" }, { ref: "file:gone/x.md" }],
      layout: { order: { agents: ["agent:retired", "agent:morning"] } },
    }), sections());
    const agents = d.sections.find((s) => s.id === "agents")!.items;
    expect(agents.map((i) => i.ref)).toEqual(["agent:retired", "agent:morning"]);
    expect(agents[0]).toMatchObject({ kind: "agent", missing: true });
    expect(agents[1]).toMatchObject({ kind: "agent", name: "Morning brief", missing: false });
    const files = d.sections.find((s) => s.id === "files")!.items;
    expect(files.map((i) => i.ref)).toEqual(["file:alpha/notes/plan.md", "file:gone/x.md"]);
    expect(files[1]).toMatchObject({ kind: "file", missing: true, slug: "gone", path: "x.md", title: "x" });
  });

  it("carries proposals, saved names and the active filters; an expired filter is dropped", () => {
    const now = Date.parse("2026-10-15T12:00:00Z");
    const d = composeDesktop(desk({
      proposals: [{ id: "p1", ops: [{ op: "hide", section: "files" }], summary: "Hide Files", reason: "unused" }],
      saved: ["Monday review"],
      layout: { filters: {
        cards: { where: "about.company == \"x\"", until: "2026-10-22T00:00:00Z" },
        files: { where: "old", until: "2026-10-01T00:00:00Z" },
      } },
    }), sections(), now);
    expect(d.proposals.map((p) => p.id)).toEqual(["p1"]);
    expect(d.saved).toEqual(["Monday review"]);
    expect(d.filters).toEqual([{ section: "cards", where: "about.company == \"x\"", until: "2026-10-22T00:00:00Z" }]);
  });

  it("survives a malformed answer from either side", () => {
    const d = composeDesktop({} as Desk, {} as DesktopSections);
    expect(d.sections.map((s) => s.id)).toEqual(["workspaces", "agents", "files", "cards"]);
    expect(d.sections.every((s) => Array.isArray(s.items))).toBe(true);
  });
});

const card = (id: string, over: Partial<CardView> = {}): CardView => ({
  id, ref: `card:${id}`, type: "Card", revision: 1, title: `Card ${id}`, about: null, state: "To do",
  states: ["To do", "Doing", "Done"], side_states: ["Dropped"], next: null, waiting: null, summary: null, due: null,
  progress: null, workspaces: [], parent: null, children: { done: 0, total: 0, items: [] }, members: [],
  face: [{ type: "stepper" }], movement: null, stalled: false,
  can: { edit: true, transition: true, review: false, members: true }, href: `/crm?record=${id}`, ...over,
});
const proposal = (id: string): CardProposal => ({
  id, kind: "create", title: "Follow up on pricing", workspace: "alpha", fields: {}, reason: "you said you'd follow up",
  author_kind: "agent", at: "2026-10-15T10:00:00Z", preview: { title: "Follow up on pricing" },
});

describe("composeDesktop — cards (D2, feature desktop_cards)", () => {
  it("without a cards key in the desk read (desktop_cards off) Cards stay hidden and nothing card-related is carried", () => {
    const d = composeDesktop(desk(), sections());
    expect(d.cards_on).toBe(false);
    expect(d.sections.find((s) => s.id === "cards")!.hidden).toBe(true);
    expect(d.card_proposals).toBeUndefined();
    expect(d.card_invites).toBeUndefined();
  });

  it("with cards in the desk read, draws the Cards section with one card tile each, in the layout's order", () => {
    const d = composeDesktop(desk({
      cards: [card("r1"), card("r2", { stalled: true })], card_proposals: [proposal("cp1")], card_invites: [card("r9")],
      layout: { order: { cards: ["card:r2"] } },
    }), sections());
    expect(d.cards_on).toBe(true);
    const cards = d.sections.find((s) => s.id === "cards")!;
    expect(cards.hidden).toBe(false);
    expect(cards.items.map((i) => i.ref)).toEqual(["card:r2", "card:r1"]);
    expect(cards.items[0]).toMatchObject({ kind: "card", id: "r2", stalled: true, title: "Card r2" });
    expect(d.card_proposals!.map((p) => p.id)).toEqual(["cp1"]);
    expect(d.card_invites!.map((c) => c.id)).toEqual(["r9"]);
  });

  it("keeps the person's hidden Cards section hidden, and drops malformed cards and proposals", () => {
    const d = composeDesktop(desk({
      cards: [card("r1"), { title: "no id" } as unknown as CardView, null as unknown as CardView],
      card_proposals: [{ kind: "create" } as unknown as CardProposal, proposal("cp2")],
      card_invites: "nope" as unknown as CardView[],
      layout: { sections: [{ id: "cards", hidden: true }] },
    }), sections());
    const cards = d.sections.find((s) => s.id === "cards")!;
    expect(cards.hidden).toBe(true);
    expect(cards.items.map((i) => i.ref)).toEqual(["card:r1"]);
    expect(d.card_proposals!.map((p) => p.id)).toEqual(["cp2"]);
    expect(d.card_invites).toEqual([]);
  });

  it("gives a card without a ref its card:<id> ref", () => {
    const d = composeDesktop(desk({ cards: [{ ...card("r3"), ref: undefined as unknown as string }] }), sections());
    expect(d.sections.find((s) => s.id === "cards")!.items[0].ref).toBe("card:r3");
  });
});

describe("placedRefs", () => {
  it("names only the agent and file placements agent-api resolves", () => {
    expect(placedRefs(desk({ placements: [{ ref: "agent:a" }, { ref: "card:r1" }, { ref: "file:s/p.md" }, { ref: "agent:a" }] })))
      .toEqual(["agent:a", "file:s/p.md"]);
  });
});

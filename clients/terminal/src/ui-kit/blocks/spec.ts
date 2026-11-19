/** BLOCKS, AS DATA — `blocks.v1`, and the home's envelope over them, `home-card.v1` (Vexa-ai/vexa#1783).
 *
 *  ONE VOCABULARY FOR THREE SURFACES (founder, 2026-10-13: "rich, expressive state, well formatted
 *  for both humans and agents"). The same blocks draw in the new-chat home, in a workspace page
 *  (fenced in its markdown as ```vexa + JSON, so the file stays plain markdown in git and to an
 *  agent) and in an agent's chat reply (the agent may answer with a fence instead of a wall of
 *  text). `parseVexaFence` reads a fence; `sanitizeHome` reads a home composition.
 *
 *  The new-chat home is a stream of cards the background `home-compose` flow writes. A card is not
 *  one of six components: it is a small tree of PRIMITIVES the composer picks from (founder ruling,
 *  2026-10-13) — `title`, `text`, `person`, `row`, `list`, `evidence`, `checklist`, `action`, and two
 *  layouts, `group` and `attach` (a card nested in a card). The six kinds (meeting, waiting,
 *  suggestion, try, result, continue) survive only as a LABEL the ranking and the feedback read.
 *
 *  This module is the terminal's half of the contract: `sanitizeHome` takes whatever the cache route
 *  returned and gives back a document the renderer can draw without a single check of its own. It
 *  never throws. Unknown blocks and props are dropped, text is capped, a card with nothing left is
 *  dropped, and an action can only send a prompt or open a chat/page by id — there is no HTML and
 *  no markdown anywhere in the spec. The server applies the same rules before it writes the cache
 *  (core/agent/services/home/spec.py); both read the same limits from the contract
 *  (core/agent/contracts/home-card.v1.json). */

export const HOME_SPEC = "home-card.v1";
export const BLOCKS_SPEC = "blocks.v1";
/** The fence language a page or a reply carries blocks under. */
export const FENCE_LANG = "vexa";

export const KINDS = ["meeting", "waiting", "suggestion", "try", "result", "continue"] as const;
export type HomeKind = (typeof KINDS)[number];

export const TONES = ["default", "suggested", "try", "muted"] as const;
export type Tone = (typeof TONES)[number];

/** The caps — the contract's `limits`. */
export const LIMITS = {
  cards: 7,          // desktop stream
  phoneCards: 5,     // phone stream
  blocks: 12,        // per card
  rows: 6,           // per list
  items: 6,          // per evidence / checklist
  actions: 4,        // per action row
  depth: 3,          // attach/group nesting, and peeks inside peeks
  showRows: 3,       // a list shows this many, then "show more"
  people: 40,
  short: 60,         // labels, meta, names
  text: 240,         // title / text / row text
  prompt: 600,       // an action's prompt
} as const;

export const ICONS = ["calendar", "mail", "chat", "clock", "repeat", "folder", "doc", "plane", "bot", "spark", "people", "mic"] as const;
export type IconName = (typeof ICONS)[number];

export type Person = {
  key: string;
  name: string;
  initials: string;
  role?: string;
  last?: string;
  reach?: string;
  /** on Vexa: an account the 1:1 shared chat can be opened with. */
  vexa: boolean;
  account_id?: string;
  email?: string;
  /** media keys agent-api fetches and caches (`/api/home/media/<key>`); the browser asks no third party */
  avatar?: string;
  logo?: string;
  /** a Vexa user's photo route (`/api/people/<id>/photo`), served with the viewer's access checked */
  photo?: string;
  /** a colleague active in a chat you share: now, or today */
  presence?: "now" | "today";
};

export type Action = {
  type: "action";
  label: string;
  primary?: boolean;
  /** the external-send gate is shown on the button: the agent drafts, the person says go. */
  needs_go?: boolean;
  prompt?: string;
  open?: { chat?: string; page?: string };
  message?: string; // person key
  /** ask the agent to elaborate THIS card or section; the answer is inserted in place as blocks */
  expand?: true;
  /** start a call with Vexa: a new chat with a one-person call; it becomes a page with its transcript */
  call?: true;
};

export type Row = { type: "row"; icon?: IconName; text: string; meta?: string; age?: string; hot?: boolean; person?: string; action?: Action };
export type CheckItem = { id: string; text: string; meta?: string; checked: boolean };

export type Block =
  | { type: "title"; text: string }
  | { type: "text"; text: string; tone?: "why" | "plain" | "ok" | "warn" }
  | { type: "person"; people: string[]; extra?: string[] }
  | Row
  | { type: "list"; rows: Row[] }
  | { type: "evidence"; items: { text: string; source: string }[] }
  | { type: "checklist"; items: CheckItem[]; action?: Action }
  | Action
  | { type: "group"; label?: string; blocks: Block[]; collapsed?: boolean; summary?: string }
  | { type: "attach"; card: Card }
  /** A FLOW, drawn read-only as a small graph (trigger → steps → outputs): a routine in its chat,
   *  or what an automation proposal would do. Definition only — no runs, no editor. */
  | { type: "flow"; flow: FlowBlock };

export type FlowBlock = {
  name: string;
  trigger: { kind: "schedule"; cron: string } | { kind: "event"; event: string };
  steps: string[];
  instruction?: string;
  notify?: string;
};

export const STATES = ["open", "acted", "in_progress", "prepared", "live", "ended", "follow_up", "replied", "done"] as const;
export type ItemState = (typeof STATES)[number];
export type Refs = { session?: string; meeting?: string; entities?: string[]; pages?: string[]; urls?: string[] };

export type Card = {
  type: "card";
  id: string;
  kind: HomeKind;
  label?: string;
  meta?: string;
  tone: Tone;
  wide: boolean;
  /** dismiss + snooze. A card the person cannot refuse (a meeting about to start) says so. */
  controls: boolean;
  rank?: { pos: number; score: number; signal?: string };
  /** feedback keys: whose card this is, for per-person weights. */
  person?: string;
  /** drawn folded: one line (`summary`, else the title) that opens in place */
  collapsed?: boolean;
  summary?: string;
  /** where the item got to — advanced by evidence, never by a click alone */
  state?: ItemState;
  result?: { chat?: string; page?: string };
  refs?: Refs;
  /** set by the client when a live frame patched this card */
  patchedAt?: number;
  blocks: Block[];
};

export type HomeDoc = {
  spec: typeof HOME_SPEC;
  composed_at: string;
  greeting: { title: string; text: string };
  cards: Card[];
  left_out?: string;
  people: Record<string, Person>;
};

// ── sanitizer ───────────────────────────────────────────────────────────────────────

type U = unknown;
const isObj = (v: U): v is Record<string, U> => typeof v === "object" && v !== null && !Array.isArray(v);
/** Plain text only: control characters out, whitespace collapsed, capped with an ellipsis. */
export function clip(v: U, max: number): string {
  if (typeof v !== "string" && typeof v !== "number") return "";
  // eslint-disable-next-line no-control-regex
  const s = String(v).replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
}
const opt = (v: U, max: number): string | undefined => { const s = clip(v, max); return s || undefined; };
const oneOf = <T extends string>(v: U, all: readonly T[], dflt?: T): T | undefined =>
  (typeof v === "string" && (all as readonly string[]).includes(v) ? (v as T) : dflt);
const arr = (v: U): U[] => (Array.isArray(v) ? v : []);
const ID = /^[A-Za-z0-9._:@+-]{1,120}$/;
const id = (v: U): string | undefined => (typeof v === "string" && ID.test(v) ? v : undefined);
/** A page opens by its workspace path — relative, no scheme, no traversal. */
const PAGE = /^(?!.*\.\.)(?![a-z]+:)[A-Za-z0-9 _./@+-]{1,200}$/;

function person(v: U, key: string): Person | null {
  if (!isObj(v)) return null;
  const name = clip(v.name, LIMITS.short);
  if (!name) return null;
  const initials = clip(v.initials, 3) || name.split(/\s+/).map((w) => w[0] ?? "").join("").slice(0, 2).toUpperCase();
  const p: Person = { key, name, initials, vexa: v.vexa === true };
  const role = opt(v.role, LIMITS.short); if (role) p.role = role;
  const last = opt(v.last, LIMITS.short); if (last) p.last = last;
  const reach = opt(v.reach, LIMITS.short); if (reach) p.reach = reach;
  const acc = id(v.account_id); if (acc) p.account_id = acc;
  const email = typeof v.email === "string" && /^[^\s@<>]{1,64}@[^\s@<>]{1,190}$/.test(v.email) ? v.email : undefined;
  if (email) p.email = email;
  const MEDIA = /^(g:[0-9a-f]{32}|d:(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})$/;
  if (typeof v.avatar === "string" && MEDIA.test(v.avatar)) p.avatar = v.avatar;
  if (typeof v.logo === "string" && MEDIA.test(v.logo)) p.logo = v.logo;
  if (v.presence === "now" || v.presence === "today") p.presence = v.presence;
  return p;
}

function action(v: U, people: Record<string, Person>): Action | null {
  if (!isObj(v)) return null;
  const label = clip(v.label, LIMITS.short);
  if (!label) return null;
  const a: Action = { type: "action", label };
  if (v.primary === true) a.primary = true;
  if (v.needs_go === true) a.needs_go = true;
  const prompt = clip(v.prompt, LIMITS.prompt);
  const msg = typeof v.message === "string" && people[v.message] ? v.message : undefined;
  const o = isObj(v.open) ? v.open : null;
  const chat = o ? id(o.chat) : undefined;
  const page = o && typeof o.page === "string" && PAGE.test(o.page) ? o.page : undefined;
  // exactly one effect: a message to a person, an open, or a prompt. An action with none is dropped.
  if (msg) a.message = msg;
  else if (chat || page) a.open = chat ? { chat } : { page };
  else if (prompt) a.prompt = prompt;
  else if (v.expand === true) a.expand = true;
  else if (v.call === true) a.call = true;
  else return null;
  return a;
}

function row(v: U, people: Record<string, Person>): Row | null {
  if (!isObj(v)) return null;
  const text = clip(v.text, LIMITS.text);
  if (!text) return null;
  const r: Row = { type: "row", text };
  const icon = oneOf(v.icon, ICONS); if (icon) r.icon = icon;
  const meta = opt(v.meta, LIMITS.short); if (meta) r.meta = meta;
  const age = opt(v.age, 12); if (age) r.age = age;
  if (v.hot === true) r.hot = true;
  if (typeof v.person === "string" && people[v.person]) r.person = v.person;
  const a = action(v.action, people); if (a) r.action = a;
  return r;
}

function block(v: U, people: Record<string, Person>, depth: number): Block | null {
  if (!isObj(v)) return null;
  switch (v.type) {
    case "title": { const text = clip(v.text, LIMITS.text); return text ? { type: "title", text } : null; }
    case "text": {
      const text = clip(v.text, LIMITS.text);
      if (!text) return null;
      const tone = oneOf(v.tone, ["why", "plain", "ok", "warn"] as const);
      return tone ? { type: "text", text, tone } : { type: "text", text };
    }
    case "person": {
      const ps = arr(v.people).filter((k): k is string => typeof k === "string" && !!people[k]).slice(0, 6);
      const extra = arr(v.extra).map((x) => clip(x, 30)).filter(Boolean).slice(0, 3);
      if (!ps.length && !extra.length) return null;
      return extra.length ? { type: "person", people: ps, extra } : { type: "person", people: ps };
    }
    case "row": return row(v, people);
    case "list": {
      const rows = arr(v.rows).map((r) => row(r, people)).filter((r): r is Row => !!r).slice(0, LIMITS.rows);
      return rows.length ? { type: "list", rows } : null;
    }
    case "evidence": {
      const items = arr(v.items).flatMap((it) => {
        if (!isObj(it)) return [];
        const text = clip(it.text, LIMITS.text);
        return text ? [{ text, source: clip(it.source, 24) || "source" }] : [];
      }).slice(0, LIMITS.items);
      return items.length ? { type: "evidence", items } : null;
    }
    case "checklist": {
      const items = arr(v.items).flatMap((it, i) => {
        if (!isObj(it)) return [];
        const text = clip(it.text, LIMITS.text);
        if (!text) return [];
        const c: CheckItem = { id: id(it.id) ?? `i${i}`, text, checked: it.checked !== false };
        const meta = opt(it.meta, 24); if (meta) c.meta = meta;
        return [c];
      }).slice(0, LIMITS.items);
      if (!items.length) return null;
      const a = action(v.action, people);
      return a ? { type: "checklist", items, action: a } : { type: "checklist", items };
    }
    case "action": return action(v, people);
    case "group": {
      if (depth >= LIMITS.depth) return null;
      const blocks = blocksOf(v.blocks, people, depth + 1);
      if (!blocks.length) return null;
      const g: Extract<Block, { type: "group" }> = { type: "group", blocks };
      const label = opt(v.label, LIMITS.short); if (label) g.label = label;
      if (v.collapsed === true) g.collapsed = true;
      const summary = opt(v.summary, LIMITS.text); if (summary) g.summary = summary;
      return g;
    }
    case "attach": {
      if (depth >= LIMITS.depth) return null;
      const c = card(v.card, people, depth + 1, "attach");
      return c ? { type: "attach", card: c } : null;
    }
    case "flow": return flowBlock(v.flow);
    default: return null;
  }
}

const STEP_NAME = /^[a-z][a-z0-9_.]{0,59}$/;
/** A flow block's definition, capped like every other block: a name, a schedule (5-field cron) or an
 *  event, at most 8 step names from the step vocabulary's shape, plain-text instruction. */
export function flowBlock(v: U): Block | null {
  if (!isObj(v)) return null;
  const name = clip(v.name, LIMITS.short);
  const t = isObj(v.trigger) ? v.trigger : {};
  const trigger = t.kind === "schedule" && clip(t.cron, LIMITS.short).split(" ").length === 5
    ? { kind: "schedule" as const, cron: clip(t.cron, LIMITS.short) }
    : t.kind === "event" && clip(t.event, LIMITS.short) ? { kind: "event" as const, event: clip(t.event, LIMITS.short) } : null;
  const steps = (Array.isArray(v.steps) ? v.steps : []).filter((x): x is string => typeof x === "string" && STEP_NAME.test(x)).slice(0, 8);
  if (!name || !trigger || !steps.length) return null;
  const flow: FlowBlock = { name, trigger, steps };
  const instruction = opt(v.instruction, LIMITS.prompt); if (instruction) flow.instruction = instruction;
  const notify = opt(v.notify, LIMITS.short); if (notify) flow.notify = notify;
  return { type: "flow", flow };
}

function blocksOf(v: U, people: Record<string, Person>, depth: number): Block[] {
  const out: Block[] = [];
  let actions = 0;
  for (const b of arr(v).slice(0, LIMITS.blocks * 2)) {
    const s = block(b, people, depth);
    if (!s) continue;
    if (s.type === "action" && ++actions > LIMITS.actions) continue;
    out.push(s);
    if (out.length >= LIMITS.blocks) break;
  }
  return out;
}

function card(v: U, people: Record<string, Person>, depth: number, fallbackId: string): Card | null {
  if (!isObj(v)) return null;
  const blocks = blocksOf(v.blocks, people, depth);
  if (!blocks.length) return null;
  const kind = oneOf(v.kind, KINDS, "suggestion")!;
  const dfltTone: Tone = kind === "suggestion" ? "suggested" : kind === "try" ? "try" : kind === "continue" || kind === "result" ? "muted" : "default";
  const c: Card = {
    type: "card",
    id: id(v.id) ?? fallbackId,
    kind,
    tone: oneOf(v.tone, TONES, dfltTone)!,
    wide: v.wide === true,
    controls: v.controls !== false,
    blocks,
  };
  const label = opt(v.label, LIMITS.short); if (label) c.label = label;
  const meta = opt(v.meta, LIMITS.short); if (meta) c.meta = meta;
  if (typeof v.person === "string" && people[v.person]) c.person = v.person;
  if (v.collapsed === true) c.collapsed = true;
  const summary = opt(v.summary, LIMITS.text); if (summary) c.summary = summary;
  const st = oneOf(v.state, STATES); if (st) c.state = st;
  if (isObj(v.result)) {
    const chat = id(v.result.chat);
    const page = typeof v.result.page === "string" && PAGE.test(v.result.page) ? v.result.page : undefined;
    if (chat) c.result = { chat }; else if (page) c.result = { page };
  }
  if (isObj(v.refs)) {
    const r: Refs = {};
    const session = id(v.refs.session); if (session) r.session = session;
    const meeting = id(v.refs.meeting); if (meeting) r.meeting = meeting;
    const paths = (x: U) => arr(x).filter((p): p is string => typeof p === "string" && PAGE.test(p)).slice(0, 4);
    const ents = paths(v.refs.entities); if (ents.length) r.entities = ents;
    const pages = paths(v.refs.pages); if (pages.length) r.pages = pages;
    const urls = arr(v.refs.urls).filter((u): u is string => typeof u === "string" && /^https:\/\/[^\s<>"']{1,300}$/.test(u)).slice(0, 3);
    if (urls.length) r.urls = urls;
    if (Object.keys(r).length) c.refs = r;
  }
  if (isObj(v.rank) && Number.isFinite(v.rank.pos) && Number.isFinite(v.rank.score)) {
    c.rank = { pos: Math.max(1, Math.round(Number(v.rank.pos))), score: Math.max(0, Math.min(1, Number(v.rank.score))) };
    const sig = opt(v.rank.signal, LIMITS.short); if (sig) c.rank.signal = sig;
  }
  return c;
}

function peopleOf(raw: U): Record<string, Person> {
  const people: Record<string, Person> = {};
  if (isObj(raw)) {
    for (const [k, v] of Object.entries(raw).slice(0, LIMITS.people)) {
      if (!ID.test(k)) continue;
      const p = person(v, k);
      if (p) people[k] = p;
    }
  }
  return people;
}

/** A page's or a reply's blocks: `{"v":1,"people":{…},"blocks":[…]}` (blocks.v1 `Fence`). */
export type Fence = { v: 1; people: Record<string, Person>; blocks: Block[] };

export function sanitizeFence(raw: U): Fence | null {
  try {
    if (!isObj(raw) || (raw.v !== 1 && raw.v !== undefined)) return null;
    const people = peopleOf(raw.people);
    const blocks = blocksOf(raw.blocks, people, 0);
    return blocks.length ? { v: 1, people, blocks } : null;
  } catch {
    return null;
  }
}

/** The text inside a ```vexa fence → blocks, or null (the caller then shows the text as code). */
export function parseVexaFence(text: string): Fence | null {
  if (typeof text !== "string" || text.length > 200_000) return null;
  try { return sanitizeFence(JSON.parse(text)); } catch { return null; }
}

/** Whatever came back → a document that is safe to draw, or null when nothing in it is. */
export function sanitizeHome(raw: U): HomeDoc | null {
  try {
    if (!isObj(raw) || raw.spec !== HOME_SPEC) return null;
    const people = peopleOf(raw.people);
    const seen = new Set<string>();
    const cards: Card[] = [];
    for (const [i, c] of arr(raw.cards).slice(0, LIMITS.cards * 2).entries()) {
      const s = card(c, people, 0, `c${i}`);
      if (!s || seen.has(s.id)) continue;
      seen.add(s.id);
      cards.push(s);
      if (cards.length >= LIMITS.cards) break;
    }
    const g = isObj(raw.greeting) ? raw.greeting : {};
    const title = clip(g.title, LIMITS.short);
    if (!title && !cards.length) return null;
    const doc: HomeDoc = {
      spec: HOME_SPEC,
      composed_at: typeof raw.composed_at === "string" ? raw.composed_at.slice(0, 40) : "",
      greeting: { title, text: clip(g.text, LIMITS.text) },
      cards,
      people,
    };
    const left = opt(raw.left_out, LIMITS.text); if (left) doc.left_out = left;
    return doc;
  } catch {
    return null;
  }
}

/** The stream as drawn: the phone cuts at five and says how many it cut. */
export function shownCards(doc: HomeDoc, phone: boolean): { cards: Card[]; cut: number } {
  const max = phone ? LIMITS.phoneCards : LIMITS.cards;
  return { cards: doc.cards.slice(0, max), cut: Math.max(0, doc.cards.length - max) };
}

/** Every person a card names, for its feedback signal. */
export function cardPeople(c: Card): string[] {
  const out = new Set<string>();
  if (c.person) out.add(c.person);
  const walk = (bs: Block[]) => {
    for (const b of bs) {
      if (b.type === "person") b.people.forEach((p) => out.add(p));
      else if (b.type === "row" && b.person) out.add(b.person);
      else if (b.type === "list") b.rows.forEach((r) => r.person && out.add(r.person));
      else if (b.type === "group") walk(b.blocks);
    }
  };
  walk(c.blocks);
  return [...out];
}

/** The sign-in arrival that used to open on a greeting turn: on the home it opens on the home. */
export function homeFirstVisit(sc: { kind?: string } | null | undefined): boolean {
  return sc?.kind === "first-visit";
}

/** A live frame (`home.item`) applied to the stream in place: the card's state and the work it
 *  continues change; the order does not (a reorder waits for the next composition). A terminal
 *  state takes the card off. Pure. */
export function patchHome(doc: HomeDoc, f: { item: string; state: string; chat?: string; page?: string }, now: number): HomeDoc {
  const state = (STATES as readonly string[]).includes(f.state) ? (f.state as ItemState) : null;
  if (!state) return doc;
  if (state === "done" || state === "replied") return { ...doc, cards: doc.cards.filter((c) => c.id !== f.item) };
  return {
    ...doc,
    cards: doc.cards.map((c) => {
      if (c.id !== f.item) return c;
      const chat = id(f.chat);
      const page = typeof f.page === "string" && PAGE.test(f.page) ? f.page : undefined;
      const result = chat ? { chat } : page ? { page } : c.result;
      let blocks = c.blocks;
      if (result) {
        const go: Action = { type: "action", label: "Continue", primary: true, open: result.chat ? { chat: result.chat } : { page: result.page } };
        let done = false;
        blocks = c.blocks.map((b) => {
          if (b.type !== "action") return b;
          if (!done && (b.prompt || b.open)) { done = true; return go; }
          return b.primary ? { ...b, primary: false } : b;
        });
        if (!done) blocks = [...blocks, go];
      }
      return { ...c, state, ...(result ? { result } : {}), blocks, patchedAt: now };
    }),
  };
}

/** A page, for a peek: its front-matter properties (first four), its first paragraph as the
 *  summary, and the blocks of its first ```vexa fence. Pure; reads markdown, never runs it. */
export function peekFromMarkdown(text: string): { summary?: string; properties: [string, string][]; fence: Fence | null } {
  const props: [string, string][] = [];
  let body = typeof text === "string" ? text : "";
  const fm = body.match(/^---\n([\s\S]*?)\n---\n?/);
  if (fm) {
    for (const line of fm[1].split("\n")) {
      const m = line.match(/^([A-Za-z][\w -]{0,30}):\s*(.+)$/);
      if (m && props.length < 4) props.push([m[1].trim(), clip(m[2].replace(/^["']|["']$/g, ""), LIMITS.short)]);
    }
    body = body.slice(fm[0].length);
  }
  const fenceM = body.match(/```(?:vexa)\s*\n([\s\S]*?)\n```/i);
  const fence = fenceM ? parseVexaFence(fenceM[1]) : null;
  const para = body.replace(/```[\s\S]*?```/g, "").split(/\n\s*\n/).map((p) => p.trim())
    .find((p) => p && !p.startsWith("#") && !p.startsWith("|") && !p.startsWith("<"));
  const summary = para ? clip(para.replace(/[*_`>\[\]]/g, "").replace(/\(([^)]*)\)/g, ""), LIMITS.text) : undefined;
  return { ...(summary ? { summary } : {}), properties: props, fence };
}

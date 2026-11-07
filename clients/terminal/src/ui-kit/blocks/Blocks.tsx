"use client";
/** THE ONE BLOCKS RENDERER (#1783) — `blocks.v1` drawn the same way in the home, in a workspace
 *  page (a ```vexa fence) and in an agent's chat reply.
 *
 *  Presentational: what a button DOES is the host's, through `BlocksHandlers` (a React context the
 *  shell provides once, so a page or a reply gets the same behaviour as the home). Without a host,
 *  a button announces itself as a window event (`vexa:blocks:act`) and nothing else happens.
 *
 *  PROGRESSIVE DISCLOSURE, the founder's "navigate and expand in specific places":
 *  · PEEK — a person (and, through `Peek`, any entity chip a host renders) opens a panel anchored to
 *    it with the summary, key properties and its own blocks, lazily; Open navigates, Esc or × folds.
 *  · COLLAPSE — every card and every labelled group folds to one line and opens in place.
 *  · SHOW MORE — a list shows its first three rows, then the rest on request, in place.
 *  · EXPAND — asks the agent to elaborate THAT card; the answer is inserted under it as blocks, with
 *    × to fold it and (where the host keeps things, a page) Keep.
 *  Nesting stops at three (cards in cards, peeks in peeks). Everything is a native button, so the
 *  keyboard reaches it; on a phone a peek is a bottom sheet. */
import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Bot, CalendarClock, ChevronDown, ChevronRight, Clock, ExternalLink, FileText, Folder, Lock, Mail, Maximize2, MessageSquare, Mic, Plane, Repeat, Sparkles, Users, X } from "lucide-react";
import { ExternalLink as SafeLink } from "../primitives/Links";
import { useHoverCard } from "../hoverCard";
import { proseSentence } from "../hoverFacts";
import { LIMITS, parseVexaFence, type Action, type Block, type Card, type IconName, type ItemState, type Person, type Row } from "./spec";
import { FlowGraph } from "../flowGraph/FlowGraph";

const ICON = { size: 15, strokeWidth: 1.6 } as const;
const ICONS: Record<IconName, ReactNode> = {
  calendar: <CalendarClock {...ICON} />, mail: <Mail {...ICON} />, chat: <MessageSquare {...ICON} />,
  clock: <Clock {...ICON} />, repeat: <Repeat {...ICON} />, folder: <Folder {...ICON} />, doc: <FileText {...ICON} />,
  plane: <Plane {...ICON} />, bot: <Bot {...ICON} />, spark: <Sparkles {...ICON} />, people: <Users {...ICON} />,
  mic: <Mic {...ICON} />,
};
export const KIND_LABEL: Record<Card["kind"], string> = {
  meeting: "Meeting", waiting: "Waiting on you", suggestion: "Suggested", try: "Try", result: "Result", continue: "Continue",
};

export type Reason = "wrong" | "done" | "not_now";
export type PeekContent = { head?: Person; summary?: string; properties?: [string, string][]; blocks?: Block[]; people?: Record<string, Person> };

export type BlocksHandlers = {
  onAction?: (card: Card | null, action: Action) => void;
  onMessage?: (person: Person, card: Card | null) => void;
  onOpenPerson?: (person: Person) => void;
  onDismiss?: (card: Card, reason: Reason) => void;
  onSnooze?: (card: Card) => void;
  /** elaborate this card: resolves to the blocks to insert under it, or null */
  onExpand?: (card: Card) => Promise<{ blocks: Block[]; people?: Record<string, Person> } | null>;
  /** keep an expansion (a page writes it into the file); absent = nothing to keep it in */
  onKeep?: (card: Card, blocks: Block[]) => void;
  /** open a workspace page (`<slug>/<path>`) — a quick link on a card, or a `page` action */
  onOpenPage?: (path: string) => void;
  /** peek a page's summary and blocks — absent = no peek, Open only */
  /** `slug` null = a desk path; undefined = `path` is `<slug>/<path>` */
  onPeekPage?: (path: string, slug?: string | null) => Promise<PeekContent | null>;
};

const STATE_LABEL: Partial<Record<ItemState, string>> = {
  acted: "In progress", in_progress: "In progress", prepared: "Prepared", live: "Live now", ended: "Ended",
  follow_up: "Follow-up drafted",
};

/** A picture agent-api fetched and cached (`/api/home/media/<key>`) — or the initials when it has
 *  none, the policy is off, or the image fails. The browser asks no third party. */
export function Avatar({ p, size = "sm" }: { p: Pick<Person, "initials" | "avatar" | "name" | "photo">; size?: "sm" | "lg" }) {
  const [broken, setBroken] = useState(false);
  const cls = `vx-blk-av${size === "lg" ? " lg" : ""}`;
  const src = p.photo || (p.avatar ? `/api/home/media/${encodeURIComponent(p.avatar)}` : "");
  if (src && !broken) {
    return <img className={`${cls} img`} src={src} alt="" aria-hidden="true"
      loading="lazy" onError={() => setBroken(true)} />;
  }
  return <span className={cls} aria-hidden="true">{p.initials}</span>;
}

function Logo({ k }: { k?: string }) {
  const [broken, setBroken] = useState(false);
  if (!k || broken) return null;
  return <img className="vx-blk-logo" src={`/api/home/media/${encodeURIComponent(k)}`} alt="" aria-hidden="true" loading="lazy" onError={() => setBroken(true)} />;
}

function ago(ms: number): string {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  return s < 45 ? "updated just now" : s < 3600 ? `updated ${Math.round(s / 60)} min ago` : "";
}

const BlocksCtx = createContext<BlocksHandlers>({});
export const BlocksProvider = BlocksCtx.Provider;
export const useBlocksHandlers = (): BlocksHandlers => useContext(BlocksCtx);

function announce(detail: unknown) {
  try { window.dispatchEvent(new CustomEvent("vexa:blocks:act", { detail })); } catch { /* no window */ }
}

type Env = { people: Record<string, Person>; depth: number; card: Card | null };

/** A list of blocks, with the people they name. Consecutive actions share one row. */
export function BlocksView({ blocks, people, depth = 0, card = null }: { blocks: Block[]; people: Record<string, Person>; depth?: number; card?: Card | null }) {
  const env: Env = { people, depth, card };
  const out: ReactNode[] = [];
  let acts: Action[] = [];
  const flush = (k: string) => {
    if (acts.length) out.push(<div key={`a${k}`} className="vx-blk-acts">{acts.map((a, i) => <ActionBtn key={i} a={a} env={env} />)}</div>);
    acts = [];
  };
  blocks.forEach((b, i) => {
    if (b.type === "action") { acts.push(b); return; }
    flush(String(i));
    out.push(<BlockView key={i} b={b} env={env} />);
  });
  flush("end");
  return <>{out}</>;
}

/** A ```vexa fence's text: the blocks when it parses, the text as code when it does not. */
export function VexaFence({ source }: { source: string }) {
  const f = parseVexaFence(source);
  if (!f) return <pre className="vx-blk-raw"><code>{source}</code></pre>;
  return <div data-vexa-fence className="vx-blk-fence"><BlocksView blocks={f.blocks} people={f.people} /></div>;
}

/** One card: header (kind label, meta, controls), body, and whatever Expand inserted under it. */
export function CardView({ card, people, depth = 0, role }: { card: Card; people: Record<string, Person>; depth?: number; role?: string }) {
  const h = useContext(BlocksCtx);
  const [folded, setFolded] = useState(!!card.collapsed);
  const [asking, setAsking] = useState(false);
  const [more, setMore] = useState<{ blocks: Block[]; people: Record<string, Person> } | "loading" | "none" | null>(null);
  const bodyId = useId();
  const cls = ["vx-blk-card", `t-${card.tone}`, card.wide && depth === 0 ? "wide" : "", depth ? "attach" : ""].filter(Boolean).join(" ");
  const line = card.summary ?? firstLine(card);
  const blocks = onePrimary(card.blocks);
  // the state, unless the meta line already says it ("ended 18:52" and an "Ended" badge is twice)
  const stateWord = card.state && STATE_LABEL[card.state] && !(card.meta ?? "").toLowerCase().includes(STATE_LABEL[card.state]!.toLowerCase().split(" ")[0])
    ? STATE_LABEL[card.state] : "";
  useEffect(() => {
    const on = (e: Event) => { if ((e as CustomEvent).detail === card.id) void expand(); };
    window.addEventListener("vexa:blocks:expand", on);
    return () => window.removeEventListener("vexa:blocks:expand", on);
  });
  const expand = async () => {
    if (!h.onExpand) return;
    setMore("loading");
    const r = await h.onExpand(card).catch(() => null);
    setMore(r && r.blocks.length ? { blocks: r.blocks, people: { ...people, ...(r.people ?? {}) } } : "none");
  };
  return (
    <article data-home-card={card.id} data-kind={card.kind} className={cls} role={role}>
      <div className="vx-blk-ih">
        <span className="vx-blk-kl">{card.label ?? KIND_LABEL[card.kind]}</span>
        {card.meta && <span className="vx-blk-meta">{card.meta}</span>}
        {stateWord && <span data-state={card.state} className="vx-blk-state">{stateWord}</span>}
        {card.patchedAt && ago(card.patchedAt) && <span className="vx-blk-updated" role="status">{ago(card.patchedAt)}</span>}
        {/* the card's own controls: drawn on hover or focus only (always on touch), so a stream of
            cards does not carry a row of icons each (founder, UI lab 2026-10-14) */}
        <span className={`vx-blk-ctl${asking || folded ? " on" : ""}`}>
          <button type="button" className="vx-blk-ghost" aria-expanded={!folded} aria-controls={bodyId}
            aria-label={folded ? "Expand section" : "Collapse section"} onClick={() => setFolded((f) => !f)}>
            {folded ? <ChevronRight {...ICON} /> : <ChevronDown {...ICON} />}
          </button>
          {h.onExpand && depth < LIMITS.depth - 1 && (
            <button type="button" className="vx-blk-ghost" aria-label="Expand: ask the agent to elaborate" title="Elaborate this"
              onClick={() => void expand()} disabled={more === "loading"}><Maximize2 {...ICON} /></button>
          )}
          {card.controls && depth === 0 && h.onSnooze && (
            <button type="button" className="vx-blk-ghost" aria-label="Snooze for a day" title="Snooze for a day"
              onClick={() => h.onSnooze?.(card)}><Clock {...ICON} /></button>
          )}
          {card.controls && depth === 0 && h.onDismiss && (
            <button type="button" className="vx-blk-ghost" aria-label="Dismiss" title="Dismiss" aria-expanded={asking}
              onClick={() => setAsking((a) => !a)}><X {...ICON} /></button>
          )}
        </span>
      </div>
      {asking && (
        <div data-home-why className="vx-blk-why" role="group" aria-label="Why dismiss?">
          <span>Why?</span>
          {([["wrong", "Wrong"], ["done", "Done already"], ["not_now", "Not now"]] as [Reason, string][]).map(([r, l]) => (
            <button key={r} type="button" className="vx-blk-btn" data-reason={r} onClick={() => { setAsking(false); h.onDismiss?.(card, r); }}>{l}</button>
          ))}
        </div>
      )}
      {folded
        ? <button type="button" data-folded className="vx-blk-summary" onClick={() => setFolded(false)}>{line}</button>
        : <div id={bodyId} className="vx-blk-body"><BlocksView blocks={blocks} people={people} depth={depth} card={card} />
            <QuickLinks card={card} depth={depth} /></div>}
      {more === "loading" && <div className="vx-blk-note" role="status">Elaborating…</div>}
      {more === "none" && <div className="vx-blk-note" role="status">Nothing more to add here.</div>}
      {more && typeof more === "object" && (
        <div data-expanded className="vx-blk-expanded">
          <div className="vx-blk-ih">
            <span className="vx-blk-kl">Elaborated</span>
            <span className="vx-blk-ctl">
              {h.onKeep && <button type="button" className="vx-blk-btn" onClick={() => h.onKeep?.(card, more.blocks)}>Keep</button>}
              <button type="button" className="vx-blk-ghost" aria-label="Fold the elaboration" onClick={() => setMore(null)}><X {...ICON} /></button>
            </span>
          </div>
          <BlocksView blocks={more.blocks} people={more.people} depth={depth + 1} card={card} />
        </div>
      )}
    </article>
  );
}

/** What the card is about, one click away: the meeting's chat, entity and workspace pages (Peek and
 *  Open), outside links. The chat the work lives in is the card's own Continue button, not a link. */
function QuickLinks({ card, depth }: { card: Card; depth: number }) {
  const h = useContext(BlocksCtx);
  const r = card.refs;
  if (!r) return null;
  const meetingChat = r.meeting ? `meet-${r.meeting}` : undefined;
  const paths = [...(r.entities ?? []), ...(r.pages ?? [])];
  const go = (a: Action) => (h.onAction ? h.onAction(card, a) : announce({ card: card.id, action: a }));
  if (!paths.length && !(r.urls ?? []).length && !(meetingChat && meetingChat !== card.result?.chat)) return null;
  return (
    <div className="vx-blk-links" aria-label="Related">
      {meetingChat && meetingChat !== card.result?.chat && (
        <button type="button" className="vx-blk-link" onClick={() => go({ type: "action", label: "Meeting", open: { chat: meetingChat } })}>
          <CalendarClock {...ICON} aria-hidden="true" />Meeting</button>
      )}
      {paths.map((p) => {
        const name = (p.split("/").pop() || p).replace(/\.md$/i, "").replace(/[-_]+/g, " ");
        return h.onPeekPage
          ? <PageLinkChip key={p} path={p} name={name} />
          : <button key={p} type="button" className="vx-blk-link" onClick={() => (h.onOpenPage ? h.onOpenPage(p) : go({ type: "action", label: name, open: { page: p } }))}>
              <FileText {...ICON} aria-hidden="true" />{name}</button>;
      })}
      {(r.urls ?? []).map((u) => (
        <SafeLink key={u} className="vx-blk-link" href={u}>
          <ExternalLink {...ICON} aria-hidden="true" />{u.replace(/^https:\/\//, "").split("/")[0]}</SafeLink>
      ))}
    </div>
  );
}

/** AT MOST ONE PRIMARY BUTTON A CARD: the first one the card marks primary stays primary, every
 *  later one is drawn as a quiet secondary (founder, UI lab 2026-10-14). */
export function onePrimary(blocks: Block[]): Block[] {
  let seen = false;
  const fix = (a: Action): Action => {
    if (!a.primary) return a;
    if (seen) return { ...a, primary: false };
    seen = true;
    return a;
  };
  return blocks.map((b) => {
    if (b.type === "action") return fix(b);
    if (b.type === "row" && b.action) return { ...b, action: fix(b.action) };
    return b;
  });
}

/** A page a card names: hover shows the small card (its first line), a click opens the page. */
function PageLinkChip({ path, name }: { path: string; name: string }) {
  const h = useContext(BlocksCtx);
  const hover = useHoverCard({ name, mark: <FileText {...ICON} aria-hidden="true" />, cacheKey: `page:${path}`,
    load: async () => { const c = await h.onPeekPage?.(path); const line = proseSentence(c?.summary ?? ""); return line ? [line] : []; } });
  return (
    <>
      <button type="button" className="vx-blk-link" {...hover.bind} onClick={() => h.onOpenPage?.(path)}>
        <FileText {...ICON} aria-hidden="true" />{name}</button>
      {hover.card}
    </>
  );
}

function firstLine(c: Card): string {
  for (const b of c.blocks) if (b.type === "title" || b.type === "text" || b.type === "row") return b.text;
  return c.label ?? KIND_LABEL[c.kind];
}

function BlockView({ b, env }: { b: Block; env: Env }) {
  switch (b.type) {
    case "title": return <div className="vx-blk-title">{b.text}</div>;
    case "text": return <div className={`vx-blk-text${b.tone ? ` ${b.tone}` : ""}`}>{b.text}</div>;
    case "person":
      return (
        <div className="vx-blk-sub">
          {b.people.map((k) => env.people[k] ? <PersonChip key={k} p={env.people[k]} card={env.card} depth={env.depth} /> : null)}
          {(b.extra ?? []).map((x, i) => <span key={`x${i}`} className="vx-blk-chip more">{x}</span>)}
        </div>
      );
    case "row": return <RowView r={b} env={env} />;
    case "list": return <ListView rows={b.rows} env={env} />;
    case "evidence":
      return <ul className="vx-blk-ev" aria-label="Evidence">{b.items.map((it, i) => <li key={i}><span>{it.text}</span><span className="src">{it.source}</span></li>)}</ul>;
    case "checklist":
      return (
        <div className="vx-blk-check">
          {b.items.map((it) => (
            <div key={it.id} className="vx-blk-prop"><span className={`box${it.checked ? " on" : ""}`} aria-hidden="true" />
              <span>{it.text}</span>{it.meta && <span className="vx-blk-meta">{it.meta}</span>}</div>
          ))}
          {b.action && <div className="vx-blk-acts"><ActionBtn a={b.action} env={env} /></div>}
        </div>
      );
    case "group": return <GroupView g={b} env={env} />;
    case "attach": return <CardView card={b.card} people={env.people} depth={env.depth + 1} />;
    case "flow": return <FlowGraph flow={{ ...b.flow }} />;
    default: return null;
  }
}

function GroupView({ g, env }: { g: Extract<Block, { type: "group" }>; env: Env }) {
  const [folded, setFolded] = useState(!!g.collapsed);
  const id = useId();
  if (!g.label && !g.collapsed) return <div className="vx-blk-group"><BlocksView blocks={g.blocks} people={env.people} depth={env.depth + 1} card={env.card} /></div>;
  return (
    <div className="vx-blk-group">
      <button type="button" className="vx-blk-grouphead" aria-expanded={!folded} aria-controls={id} onClick={() => setFolded((f) => !f)}>
        {folded ? <ChevronRight {...ICON} /> : <ChevronDown {...ICON} />}<span className="vx-blk-kl">{g.label ?? "Section"}</span>
        {folded && g.summary && <span className="vx-blk-meta">{g.summary}</span>}
      </button>
      {!folded && <div id={id}><BlocksView blocks={g.blocks} people={env.people} depth={env.depth + 1} card={env.card} /></div>}
    </div>
  );
}

function ListView({ rows, env }: { rows: Row[]; env: Env }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, LIMITS.showRows);
  return (
    <div className="vx-blk-list">
      {shown.map((r, i) => <RowView key={i} r={r} env={env} />)}
      {rows.length > shown.length && (
        <button type="button" className="vx-blk-more" data-show-more onClick={() => setAll(true)}>Show {rows.length - shown.length} more</button>
      )}
    </div>
  );
}

function RowView({ r, env }: { r: Row; env: Env }) {
  const p = r.person ? env.people[r.person] : undefined;
  return (
    <div className="vx-blk-row">
      {r.icon && <span className="vx-blk-ico" aria-hidden="true">{ICONS[r.icon]}</span>}
      {p && <PersonChip p={p} card={env.card} depth={env.depth} />}
      <span className="tx">{r.text}</span>
      {r.meta && <span className="vx-blk-meta">{r.meta}</span>}
      {r.age && <span className={`vx-blk-age${r.hot ? " hot" : ""}`}>{r.age}</span>}
      {r.action && <ActionBtn a={r.action} env={env} />}
    </div>
  );
}

function ActionBtn({ a, env }: { a: Action; env: Env }) {
  const h = useContext(BlocksCtx);
  const cls = `vx-blk-btn${a.primary ? " primary" : ""}${a.needs_go ? " gate" : ""}`;
  const person = a.message ? env.people[a.message] : undefined;
  const click = () => {
    if (person) { if (h.onMessage) h.onMessage(person, env.card); else announce({ message: person.key }); return; }
    if (a.open?.page && h.onOpenPage) { h.onOpenPage(a.open.page); return; }
    if (a.expand && env.card && h.onExpand) { window.dispatchEvent(new CustomEvent("vexa:blocks:expand", { detail: env.card.id })); return; }
    if (h.onAction) h.onAction(env.card, a); else announce({ card: env.card?.id ?? null, action: a });
  };
  return (
    <button type="button" className={cls} data-home-action={a.label}
      title={a.needs_go ? "Minutes drafts it; nothing is sent without your go" : undefined} onClick={click}>
      {a.needs_go && <Lock {...ICON} aria-hidden="true" />}{a.label}
    </button>
  );
}

/** PEEK — a panel anchored to its trigger: summary, key properties and the target's own blocks,
 *  loaded when it opens. Open navigates; Esc or × folds and gives focus back to the trigger. Hover
 *  or focus opens it on a desktop; a tap toggles it, and on a phone it is a bottom sheet. */
export function Peek({ trigger, label, load, onOpen, actions, depth = 0, hover = true, peekKey }: {
  trigger: ReactNode; label: string; load: () => Promise<PeekContent | null> | PeekContent | null;
  onOpen?: () => void; actions?: ReactNode; depth?: number; hover?: boolean; peekKey?: string;
}) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState<PeekContent | null | "loading">(null);
  const t = useRef<number | undefined>(undefined);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLSpanElement>(null);
  const id = useId();
  useEffect(() => () => window.clearTimeout(t.current), []);
  useEffect(() => {
    if (!open || content !== null) return;
    setContent("loading");
    void Promise.resolve(load()).then((c) => setContent(c ?? {})).catch(() => setContent({}));
  }, [open, content, load]);
  const quiet = useRef(false);   // focus handed back on fold must not reopen it
  const fold = (focusBack = false) => {
    setOpen(false);
    if (focusBack) { quiet.current = true; btn.current?.focus(); quiet.current = false; }
  };
  const show = () => { if (quiet.current) return; window.clearTimeout(t.current); setOpen(true); };
  const hide = () => { t.current = window.setTimeout(() => setOpen(false), 200); };
  if (depth >= LIMITS.depth) {
    return <button type="button" className="vx-blk-chip person" onClick={onOpen}>{trigger}</button>;
  }
  const c = content && content !== "loading" ? content : null;
  return (
    <span className="vx-blk-pwrap" onMouseEnter={hover ? show : undefined} onMouseLeave={hover ? hide : undefined}
      onKeyDown={(e) => { if (e.key === "Escape" && open) { e.stopPropagation(); fold(true); } }}>
      <button ref={btn} type="button" className="vx-blk-chip person" data-person={peekKey} aria-expanded={open} aria-controls={id}
        aria-haspopup="dialog" onClick={() => (open ? fold() : show())} onFocus={hover ? show : undefined}>
        {trigger}
      </button>
      {open && (
        <span ref={panel} id={id} data-person-card={peekKey} data-peek className="vx-blk-hc" role="dialog" aria-label={label}>
          <span className="vx-blk-hc-x">
            <button type="button" className="vx-blk-ghost" aria-label="Close" onClick={() => fold(true)}><X {...ICON} /></button>
          </span>
          {content === "loading" && <span className="vx-blk-note" role="status">Loading…</span>}
          {c?.head && (
            <span className="top"><Avatar p={c.head} size="lg" />
              <span><span className="nm">{c.head.name}</span><Logo k={c.head.logo} /></span></span>
          )}
          {c?.summary && <span className="vx-blk-hc-sum">{c.summary}</span>}
          {c?.properties?.length ? (
            <span className="dl">{c.properties.map(([k, v]) => <span key={k} className="kv"><span className="dt">{k}</span><span>{v}</span></span>)}</span>
          ) : null}
          {c?.blocks?.length ? <span className="vx-blk-hc-blocks"><BlocksView blocks={c.blocks} people={c.people ?? {}} depth={depth + 1} /></span> : null}
          <span className="vx-blk-acts">
            {actions}
            {onOpen && <button type="button" className="vx-blk-btn" data-peek-open onClick={() => { fold(); onOpen(); }}><ExternalLink {...ICON} aria-hidden="true" />Open</button>}
          </span>
        </span>
      )}
    </span>
  );
}

/** A person, everywhere (founder 2026-10-15: "small and only on hover, click should open page"):
 *  hovering or focusing shows the small hover card — role · organisation, how you are connected,
 *  whether they are on Vexa — and a click opens their page. Messaging them is the card's own
 *  Message button, never a popover on the chip. */
export function PersonChip({ p, card: _card, depth: _depth = 0 }: { p: Person; card: Card | null; depth?: number }) {
  const h = useContext(BlocksCtx);
  const lines = [p.role ?? "", p.last ? `Last touch ${p.last}` : "", p.vexa ? (p.presence === "now" ? "On Vexa · active now" : "On Vexa") : (p.reach ? `Reach: ${p.reach}` : "")].filter(Boolean);
  const hover = useHoverCard({ name: p.name, mark: <Avatar p={p} />, lines, cacheKey: `person:${p.key ?? p.name}` });
  const open = () => (h.onOpenPerson ? h.onOpenPerson(p) : announce({ open_person: p.key }));
  return (
    <>
      <button type="button" className="vx-blk-chip person" data-person={p.key} {...hover.bind} onClick={open}>
        <Avatar p={p} />{p.vexa && <span className={`vx-blk-vx${p.presence ? ` ${p.presence}` : ""}`} aria-hidden="true" />}{p.name}
      </button>
      {hover.card}
    </>
  );
}

/** THE PAGE PEEK the rich-pages seam (`doc.peek`) draws after an entity chip or page link: summary,
 *  key properties and the page's own blocks, loaded lazily through the host (`onPeekPage`). Open
 *  navigates; Esc or × folds and hands focus back. A person's page shows their initials up top. */
export function PagePeekPanel({ params }: { params?: Record<string, unknown> }) {
  const h = useContext(BlocksCtx);
  const path = String(params?.path ?? "");
  const slug = typeof params?.slug === "string" ? params.slug : "";
  const title = typeof params?.title === "string" && params.title ? params.title : (path.split("/").pop() || path).replace(/\.md$/i, "");
  const onOpen = typeof params?.onOpen === "function" ? (params.onOpen as () => void) : undefined;
  const onClose = typeof params?.onClose === "function" ? (params.onClose as () => void) : undefined;
  const [c, setC] = useState<PeekContent | null | "loading">("loading");
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    let live = true;
    ref.current?.focus();
    const load = h.onPeekPage ? h.onPeekPage(path, slug || null) : Promise.resolve(null);
    void Promise.resolve(load).then((v) => { if (live) setC(v ?? {}); }).catch(() => { if (live) setC({}); });
    return () => { live = false; };
  }, [path, slug]); // eslint-disable-line react-hooks/exhaustive-deps
  const person = params?.kind === "person";
  const initials = title.split(/\s+/).map((w) => w[0] ?? "").join("").slice(0, 2).toUpperCase();
  const v = c && c !== "loading" ? c : null;
  return (
    <span ref={ref} tabIndex={-1} data-page-peek={path} className="vx-blk-peek" role="dialog" aria-label={title}
      onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose?.(); } }}>
      <span className="vx-blk-hc-x"><button type="button" className="vx-blk-ghost" aria-label="Close" onClick={() => onClose?.()}><X {...ICON} /></button></span>
      <span className="top">{person && <Avatar p={{ name: title, initials }} size="lg" />}<span className="nm">{title}</span></span>
      {c === "loading" && <span className="vx-blk-note" role="status">Loading…</span>}
      {v?.summary && <span className="vx-blk-hc-sum">{v.summary}</span>}
      {v?.properties?.length ? <span className="dl">{v.properties.map(([k, val]) => <span key={k} className="kv"><span className="dt">{k}</span><span>{val}</span></span>)}</span> : null}
      {v?.blocks?.length ? <span className="vx-blk-hc-blocks"><BlocksView blocks={v.blocks} people={v.people ?? {}} depth={1} /></span> : null}
      {onOpen && <span className="vx-blk-acts"><button type="button" className="vx-blk-btn" data-peek-open onClick={() => { onClose?.(); onOpen(); }}><ExternalLink {...ICON} aria-hidden="true" />Open</button></span>}
    </span>
  );
}

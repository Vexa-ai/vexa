"use client";
/** THE CARD SHEET (7q, 7p; §11.2 F1/E2) — a card, opened.
 *
 *  A right panel on desktop; below 720px a full-screen sheet with ‹ Back top-left · title · ⋯ that
 *  stacks above the list it came from. The system back closes it (a history entry is pushed on open).
 *  Order: the decision block, STICKY at the top; the gate strip (done / current / ahead, the current
 *  actor and time at that gate); the full preview; Why; Connected (only groups that have something,
 *  each item one clickable row); History, one line per decision with a "verify" link to its revision.
 *
 *  `CardSheetHost` listens for `OPEN_CARD_SHEET_EVENT`; it may be mounted by several surfaces — only
 *  the first mounted host draws. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronLeft, Link2, Lock, MessageSquare, MoreHorizontal, User } from "lucide-react";
import type { CoreCard as CardView } from "../../platform/desktop";
import { OPEN_SESSION_EVENT } from "../../platform/events";
import { Menu } from "../../ui-kit";
import { formatDate, fullDate } from "../../ui-kit/format/date";
import { discussCard, OPEN_CARD_SHEET_EVENT, readCardCore, useCardInbox } from "./api";
import { DecisionBlock } from "./DecisionBlock";
import { useNames, useSeenInView, useWorkspaceNames } from "./hooks";
import { kindIcon, kindName } from "./CardRow";
import { ACTION_LABEL, decidedLine, originSession, originTurn, personName, waited, waitingLine, type NameOf } from "./text";
import "./cards.css";

const DONE: Record<string, string> = { acknowledge: "Accepted", decide: "Approved", confirm: "Signed", answer: "Answered", deliver: "Delivered", use: "Used", decline: "Declined" };

function Group({ title, children }: { title: string; children: ReactNode }) {
  return <section className="vx-cs-group" aria-label={title}><h3 className="vx-cs-h">{title}</h3>{children}</section>;
}
function ConnRow({ icon, pre, label, onClick }: { icon: ReactNode; pre?: string; label: string; onClick?: () => void }) {
  return <li><button type="button" className="vx-cs-conn" onClick={onClick} disabled={!onClick}>{icon}{pre && <small>{pre}</small>}<span>{label}</span></button></li>;
}

export function GateStrip({ card, nameOf }: { card: CardView; nameOf: NameOf }) {
  const gates = card.gates ?? [];
  if (!gates.length) return null;
  return <ol className="vx-cs-gates" aria-label="Gates">
    {gates.map((g) => <li key={g.name} data-gate={g.state} aria-current={g.state === "current" ? "step" : undefined}>
      <span className="vx-cs-gate-name">{g.name}</span>
      {g.state === "done" && g.by && <small>{personName(nameOf, g.by)}{g.at ? ` · ${formatDate(g.at)}` : ""}</small>}
      {g.state === "current" && (() => {
        const who = card.open !== false && card.viewer?.role === "next_actor" ? "you" : card.next_actor ? personName(nameOf, card.next_actor) : "";
        const line = [who, g.at ? waited(g.at) : ""].filter(Boolean).join(" · ");
        return line ? <small>{line}</small> : null;
      })()}
    </li>)}
  </ol>;
}

/** the full preview: the summary and the face's text blocks — everything readable, nothing cut, line
 *  breaks kept (a diff or a note reads as written). A field this viewer cannot read (`locked`) is named,
 *  never shown, so nobody signs believing they saw the whole card. */
function Preview({ card }: { card: CardView }) {
  const lines: string[] = [];
  const locked: string[] = [];
  for (const b of card.face ?? []) {
    const x = b as unknown as Record<string, unknown>;
    if (x.type === "locked") { locked.push(typeof x.label === "string" && x.label.trim() ? x.label.trim() : "A field"); continue; }
    for (const k of ["title", "text", "body", "value"]) if (typeof x[k] === "string" && (x[k] as string).trim()) lines.push(x[k] as string);
  }
  if (!card.summary && !lines.length && !locked.length) return null;
  const multi = !!card.summary && card.summary.includes("\n");
  return <Group title="Preview">
    {card.summary && (multi ? <pre className="vx-cs-pre" data-preview="summary">{card.summary}</pre> : <p className="vx-cs-prose">{card.summary}</p>)}
    {lines.length > 0 && <pre className="vx-cs-pre">{lines.join("\n")}</pre>}
    {locked.map((l, i) => <p key={`${l}-${i}`} className="vx-cs-locked" data-locked=""><Lock size={12} aria-hidden />{l} · not visible to you</p>)}
  </Group>;
}

export function CardSheet({ id, onClose }: { id: string; onClose: () => void }) {
  const inbox = useCardInbox(false);
  const fromInbox = inbox.items.find((c) => c.id === id) ?? null;
  const [card, setCard] = useState<CardView | null>(fromInbox);
  const [failed, setFailed] = useState(false);
  const nameOf = useNames();
  const wsName = useWorkspaceNames();
  const ref = useRef<HTMLDivElement | null>(null);
  useSeenInView(ref, card);

  const reload = useCallback(async () => {
    const c = await readCardCore(id);
    if (c) { setCard(c); setFailed(false); } else setFailed(true);
  }, [id]);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => { if (fromInbox && card && fromInbox.revision > card.revision) setCard(fromInbox); }, [fromInbox, card]);

  // the system back closes the sheet: one history entry per open sheet
  useEffect(() => {
    const mark = { vxCardSheet: id };
    try { history.pushState(mark, ""); } catch { /* no history in this frame */ }
    const pop = () => onClose();
    window.addEventListener("popstate", pop);
    return () => { window.removeEventListener("popstate", pop); };
  }, [id, onClose]);
  const back = () => {
    const st = history.state as { vxCardSheet?: string } | null;
    if (st?.vxCardSheet === id) history.back(); else onClose();
  };
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") back(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });

  const title = card?.title ?? (failed ? "Card" : "…");
  const Icon = kindIcon(card?.kind);
  const session = originSession(card?.origin);
  const openSession = (s: string, turn?: string | null) => window.dispatchEvent(new CustomEvent(OPEN_SESSION_EVENT, { detail: { session: s, ...(turn ? { turn } : {}) } }));
  const titleOf = (cid: string) => inbox.items.find((c) => c.id === cid)?.title ?? "A card";
  const blocks = card?.chain?.blocks ?? [], blockedBy = card?.chain?.blocked_by ?? [];
  const waitingPeople = card && card.open !== false && card.requester && card.viewer?.role !== "requester" ? [card.requester] : [];
  const decided = card ? decidedLine(card, nameOf) : null;
  const waiting = card ? waitingLine(card, nameOf) : null;

  return <div className="vx-cs-layer" data-card-sheet={id}>
    <div ref={ref} className="vx-cs" role="dialog" aria-modal="false" aria-label={title}>
      <header className="vx-cs-head">
        <button type="button" className="vx-cs-back" onClick={back} aria-label="Back"><ChevronLeft size={16} aria-hidden /><span>Back</span></button>
        <span className="vx-cs-icon" title={kindName(card?.kind)} aria-hidden><Icon size={14} strokeWidth={1.75} /></span>
        <h2 className="vx-cs-title">{title}</h2>
        <Menu label="More" align="end" trigger={<MoreHorizontal size={14} aria-hidden />} items={[
          { key: "discuss", label: "Discuss", onSelect: () => { discussCard(id); back(); } },
          { key: "copy-ref", label: "Copy reference", onSelect: () => { void navigator.clipboard?.writeText(`[[card:${id}]]`).catch(() => undefined); } },
        ]} />
      </header>
      {failed && !card && <p className="vx-cs-note">This card is not available to you.</p>}
      {card && <div className="vx-cs-body">
        {/* 1 — the decision block, sticky at the top (or who it waits on / how it ended) */}
        <div className="vx-cs-top vx-cs-sticky" data-sticky="">
          {card.workspaces?.[0] && <p className="vx-cs-ctx">{[wsName(card.workspaces[0]), card.due ? `due ${formatDate(card.due)}` : ""].filter(Boolean).join(" · ")}</p>}
          <DecisionBlock card={card} onChanged={(c) => { if (c) setCard(c); else void reload(); }} />
          {!card.viewer?.actions?.length && (decided || waiting) && <p className="vx-cs-state">{decided ?? waiting}</p>}
        </div>
        {/* 2 — gates */}
        <GateStrip card={card} nameOf={nameOf} />
        {/* 3 — the full preview */}
        <Preview card={card} />
        {/* 4 — why */}
        {card.triage && <Group title="Why"><p className="vx-cs-prose">{card.triage.by} suggests {card.triage.suggest}: {card.triage.because}.</p></Group>}
        {/* 5 — connected: only groups that have something */}
        {(session || blocks.length > 0 || blockedBy.length > 0 || waitingPeople.length > 0) && <Group title="Connected">
          {session && <ul className="vx-cs-list"><ConnRow icon={<MessageSquare size={13} aria-hidden />} pre="made in" label="the chat it came from" onClick={() => openSession(session, originTurn(card.origin))} /></ul>}
          {(blocks.length > 0 || blockedBy.length > 0) && <ul className="vx-cs-list">
            {blockedBy.map((c) => <ConnRow key={`b${c}`} icon={<Link2 size={13} aria-hidden />} pre="blocked by" label={titleOf(c)} onClick={() => window.dispatchEvent(new CustomEvent(OPEN_CARD_SHEET_EVENT, { detail: { id: c } }))} />)}
            {blocks.map((c) => <ConnRow key={`k${c}`} icon={<Link2 size={13} aria-hidden />} pre="blocks" label={titleOf(c)} onClick={() => window.dispatchEvent(new CustomEvent(OPEN_CARD_SHEET_EVENT, { detail: { id: c } }))} />)}
          </ul>}
          {waitingPeople.length > 0 && <ul className="vx-cs-list">{waitingPeople.map((p) => <ConnRow key={p} icon={<User size={13} aria-hidden />} pre="waiting:" label={personName(nameOf, p)} />)}</ul>}
        </Group>}
        {/* 6 — history, with verify links */}
        {(card.decisions ?? []).length > 0 && <Group title="History">
          <ol className="vx-cs-hist">{(card.decisions ?? []).map((d) => <li key={`${d.revision}-${d.action}`}>
            <time dateTime={d.at} title={fullDate(d.at)}>{formatDate(d.at)}</time>
            <span>{DONE[d.action] ?? ACTION_LABEL[d.action as keyof typeof ACTION_LABEL] ?? d.action} by {personName(nameOf, d.by)}</span>
            <a className="vx-cs-verify" href={`/crm?record=${encodeURIComponent(card.id)}&revision=${d.revision}`}>verify</a>
          </li>)}</ol>
        </Group>}
      </div>}
    </div>
  </div>;
}

let hostOwner: symbol | null = null;
/** Mount anywhere cards are listed; the first mounted host draws the sheet, the others stay silent. */
export function CardSheetHost() {
  const me = useRef(Symbol("card-sheet-host"));
  const [mine, setMine] = useState(false);
  const [id, setId] = useState<string | null>(null);
  useEffect(() => {
    if (!hostOwner) { hostOwner = me.current; setMine(true); }
    return () => { if (hostOwner === me.current) hostOwner = null; };
  }, []);
  useEffect(() => {
    if (!mine) return;
    const on = (e: Event) => { const v = (e as CustomEvent<{ id?: string }>).detail?.id; if (typeof v === "string" && v) setId(v); };
    window.addEventListener(OPEN_CARD_SHEET_EVENT, on);
    return () => window.removeEventListener(OPEN_CARD_SHEET_EVENT, on);
  }, [mine]);
  const close = useCallback(() => setId(null), []);
  return mine && id ? <CardSheet key={id} id={id} onClose={close} /> : null;
}

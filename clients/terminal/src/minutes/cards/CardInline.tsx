"use client";
/** A CRM CARD INLINE IN CHAT (7g, 7q) — an agent writes ```card {"card":"<id>"}.
 *
 *  Title; one context line (workspace · room · why); a preview of up to ~6 lines with "show all";
 *  then, for the next actor, the decision block. Everyone else reads "Waiting on Ana · 8 h". Once
 *  decided it folds to one line ("Signed by Ana · Thu"). The title opens the card sheet and never
 *  decides (R11). Approvals and policy changes show the three-way ask; "Always" is disabled until
 *  rule cards exist. */
import { useEffect, useRef, useState } from "react";
import { Check, X } from "lucide-react";
import type { CoreCard as CardView } from "../../platform/desktop";
import { openCardSheet, readCardCore, useCardInbox } from "./api";
import { DecisionBlock } from "./DecisionBlock";
import { useNames, useSeenInView, useWorkspaceNames } from "./hooks";
import { kindIcon, kindName } from "./CardRow";
import { decidedLine, waitingLine } from "./text";
import "./cards.css";

const PREVIEW_LINES = 6;

/** the fence's JSON → the card id, or null */
export function parseCardFence(source: string): string | null {
  try {
    const o = JSON.parse(source) as { card?: unknown };
    return typeof o?.card === "string" && /^[A-Za-z0-9._:-]{1,120}$/.test(o.card) ? o.card : null;
  } catch { return null; }
}

export function CardInlineView({ card, onChanged }: { card: CardView; onChanged?: (c: CardView | null) => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [all, setAll] = useState(false);
  const nameOf = useNames();
  const wsName = useWorkspaceNames();
  useSeenInView(ref, card);
  const decided = decidedLine(card, nameOf);
  const Icon = kindIcon(card.kind);
  const title = <button type="button" className="vx-ci-title" onClick={() => openCardSheet(card.id)}>{card.title}</button>;
  if (decided) return <div ref={ref} className="vx-ci" data-card-inline={card.id} data-folded="">
    <span className="vx-ci-fold">{card.last_decision?.action === "decline" ? <X size={12} aria-hidden /> : <Check size={12} aria-hidden />}{title}<small>{decided}</small></span>
  </div>;
  const why = card.triage ? `because ${card.triage.because}` : "";
  const ctx = [card.workspaces?.[0] ? wsName(card.workspaces[0]) : "", why].filter(Boolean).join(" · ");
  const lines = (card.summary ?? "").split("\n");
  const cut = !all && lines.length > PREVIEW_LINES;
  const waiting = waitingLine(card, nameOf);
  return <div ref={ref} className="vx-ci" role="group" aria-label={`${kindName(card.kind)}: ${card.title}`} data-card-inline={card.id} data-kind={card.kind ?? undefined}>
    <div className="vx-ci-head"><span className="vx-ci-icon" title={kindName(card.kind)} aria-hidden><Icon size={14} strokeWidth={1.75} /></span>{title}</div>
    {ctx && <p className="vx-ci-ctx">{ctx}</p>}
    {card.summary && <><pre className="vx-ci-pre">{(cut ? lines.slice(0, PREVIEW_LINES) : lines).join("\n")}</pre>
      {cut && <button type="button" className="vx-ci-all" onClick={() => setAll(true)}>show all · {lines.length} lines</button>}</>}
    {card.viewer?.role === "next_actor" && card.viewer.actions.length > 0
      ? <DecisionBlock card={card} onChanged={onChanged} />
      : waiting && <p className="vx-ci-wait">{waiting}</p>}
  </div>;
}

/** the fence renderer: the card from the Inbox store when it is there, else read once */
export function CardInline({ source }: { source: string }) {
  const id = parseCardFence(source);
  const inbox = useCardInbox(false);
  const stored = id ? inbox.items.find((c) => c.id === id) ?? null : null;
  const [card, setCard] = useState<CardView | null>(stored);
  const [gone, setGone] = useState(false);
  useEffect(() => {
    if (!id) return;
    let on = true;
    void readCardCore(id).then((c) => { if (!on) return; if (c) setCard(c); else setGone(true); });
    return () => { on = false; };
  }, [id]);
  const shown = stored && card && stored.revision > card.revision ? stored : card ?? stored;
  if (!id) return null;
  if (!shown) return <div className="vx-ci" data-card-inline={id} data-loading="">{gone ? "This card is not available to you." : "…"}</div>;
  return <CardInlineView card={shown} onChanged={(c) => { if (c) setCard(c); else if (id) void readCardCore(id).then((x) => x && setCard(x)); }} />;
}

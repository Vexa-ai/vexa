"use client";
/** ONE CARD AS ONE ROW (7m) — the Now strip, the Inbox and a workspace's "Waiting on you".
 *
 *  Two lines at most: a kind icon (not a caps label), the title, then a muted tail (due · workspace ·
 *  room) or this viewer's line ("Waiting on Ana · 8 h"; folded "Signed by Ana · Thu" once decided).
 *  ONE outlined action, always in the same place: the viewer's primary action, or Open when there is
 *  none. Everything else is in "⋯" — Decline last — and in full in the card sheet. A triage
 *  suggestion is an icon with a tooltip.
 *
 *  R11: a press on the row or its title OPENS the card and never decides. No action is revealed by
 *  hover, so a touch screen never puts a destructive control under the first tap; Decline is only in
 *  "⋯" or the sheet, and it is held with Undo. Accept / Confirm / Approve act in one press from the
 *  row; Answer and Deliver need input, so they open the sheet. */
import { useRef } from "react";
import { CalendarCheck, Check, CircleHelp, Hand, MoreHorizontal, ScrollText, ShieldCheck, Sparkles, SquareCheck, UserPlus, X, type LucideIcon } from "lucide-react";
import type { CardAction, CardKind, CoreCard as CardView } from "../../platform/desktop";
import { Menu, type MenuItem } from "../../ui-kit";
import { decide, discussCard, fetchNonce, openCardSheet } from "./api";
import { holdDecline, undoDecline, useDeclineHeld, useNames, useSeenInView, useWorkspaceNames } from "./hooks";
import { ACTION_LABEL, decidedLine, primaryAction, tailOf, viewerActions, waitingLine } from "./text";
import "./cards.css";

export const KIND_ICON: Record<CardKind, LucideIcon> = {
  ask: Hand, approval: ShieldCheck, question: CircleHelp, commitment: CalendarCheck, invitation: UserPlus, policy_change: ScrollText,
};
export const KIND_NAME: Record<CardKind, string> = {
  ask: "Ask", approval: "Approval", question: "Question", commitment: "Commitment", invitation: "Invitation", policy_change: "Policy change",
};
export const kindIcon = (k: CardKind | null | undefined): LucideIcon => (k ? KIND_ICON[k] : SquareCheck);
export const kindName = (k: CardKind | null | undefined): string => (k ? KIND_NAME[k] : "Card");

/** the actions that act straight from the row (one press, no input) */
const DIRECT: CardAction[] = ["acknowledge", "confirm", "decide"];

/** Decide from a row: the nonce is fetched for the card as it is now, then the decision is posted. */
async function decideNow(card: CardView, action: CardAction): Promise<void> {
  const n = await fetchNonce(card.id);
  if (n) await decide(card.id, n, { action });
}

export function CardRow({ card, onOpen, roomName }: { card: CardView; onOpen?: (id: string) => void; roomName?: (session: string) => string | null }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const nameOf = useNames();
  const wsName = useWorkspaceNames();
  const declining = useDeclineHeld(card.id);
  useSeenInView(ref, card);
  const open = () => (onOpen ?? openCardSheet)(card.id);
  const Icon = kindIcon(card.kind);
  const decided = decidedLine(card, nameOf);
  const waiting = waitingLine(card, nameOf);
  const tail = tailOf(card, wsName, roomName);
  const acts = viewerActions(card);
  const primary = primaryAction(card);

  if (decided) return <div ref={ref} className="vx-crow" data-card={card.id} data-folded=""><div className="vx-crow-grid">
    <button type="button" className="vx-crow-main" onClick={open} aria-label={`Open the card: ${card.title}`}>
      <span className="vx-crow-icon" aria-hidden>{card.last_decision?.action === "decline" ? <X size={14} strokeWidth={1.75} /> : <Check size={14} strokeWidth={1.75} />}</span>
      <span className="vx-crow-text"><span className="vx-crow-title">{card.title}</span><span className="vx-crow-tail">{decided}</span></span>
    </button>
  </div></div>;

  const more: MenuItem[] = [
    { key: "open", label: "Open the card", onSelect: open },
    { key: "discuss", label: "Discuss", onSelect: () => discussCard(card.id) },
    { key: "copy-ref", label: "Copy reference", onSelect: () => { void navigator.clipboard?.writeText(`[[card:${card.id}]]`).catch(() => undefined); } },
    ...(acts.includes("decline") ? [{ key: "decline", label: "Decline", danger: true, separatorBefore: true,
      onSelect: () => holdDecline(card.id, () => void decideNow(card, "decline"), `Declined: ${card.title}`) }] : []),
  ];
  const act = () => {
    if (primary && DIRECT.includes(primary)) void decideNow(card, primary);
    else open();
  };
  const sugg = card.triage ? `${card.triage.by} suggests ${card.triage.suggest}: ${card.triage.because}` : null;

  return <div ref={ref} className="vx-crow" data-card={card.id} data-kind={card.kind ?? undefined} data-mine={primary ? "" : undefined} role="group" aria-label={`${kindName(card.kind)}: ${card.title}`}><div className="vx-crow-grid">
    <button type="button" className="vx-crow-main" onClick={open} aria-label={`Open the card: ${card.title}`}>
      <span className="vx-crow-icon" title={kindName(card.kind)} aria-hidden><Icon size={14} strokeWidth={1.75} /></span>
      <span className="vx-crow-text">
        <span className="vx-crow-title">{card.title}</span>
        <span className="vx-crow-tail">{[waiting, tail].filter(Boolean).join(" · ")}</span>
      </span>
    </button>
    <span className="vx-crow-slot">{sugg ? <span role="img" title={sugg} aria-label={`Suggests ${card.triage!.suggest}`}><Sparkles size={12} aria-hidden /></span> : null}</span>
    <span className="vx-crow-act">
      {declining
        ? <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" onClick={() => undoDecline(card.id)}>Undo</button>
        : <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" data-primary={primary ? "" : undefined} data-action={primary ?? "open"}
          onClick={act}>{primary ? ACTION_LABEL[primary] : "Open"}</button>}
    </span>
    <span className="vx-crow-more"><Menu label="More" align="end" items={more} trigger={<MoreHorizontal size={14} strokeWidth={1.75} aria-hidden />} /></span>
  </div></div>;
}

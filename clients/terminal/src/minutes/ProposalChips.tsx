"use client";
/** The proposal chips — one row, in the void an empty chat leaves between its greeting and the
 *  composer. Presentational only: what to offer is `proposals()`, what a click does is the shell's.
 *
 *  A click FIRES (founder ruling, consistency with the emailed links): the turn goes, with nothing
 *  left in the composer to press Enter on. And it fires IN THIS CHAT — a chip never opens a second
 *  conversation (founder, 2026-09-01: "this chat is already new"). Every chip is a native <button>,
 *  so Tab reaches it and Enter or Space presses it; focus shows the same highlight as hover. The row is passed empty once a
 *  chip has been pressed, so the offer cannot be taken twice while its turn is still settling.
 *
 *  A CHIP WRITTEN BY AN AGENT SAYS WHERE IT CAME FROM, AND CAN BE REFUSED (Vexa-ai/vexa#1614). The
 *  derived and standing chips are statements about the product; a `jtbd` chip is a job somebody
 *  else's turn decided you had, so it carries two things the others do not need: its SOURCE, in
 *  human words, rendered beside the act — and a `×`, because a list you cannot say no to stops
 *  being a list and becomes a nag. Dismissing does NOT spend the row: the item leaves, the rest of
 *  the offer stays exactly where it was.
 *
 *  EVERY CHIP LEADS WITH AN ICON (founder 2026-10-13: "hints should have icons") — the entity chips'
 *  icon slot (`vx-chip-icon`: 14px lucide, stroke 1.75, the dim text colour), one per kind, so the
 *  act reads before the words: a calendar for prep, mail for the email check, a plug for connect, a
 *  mic for a voice note. The label is what is cut at phone width, never the icon. */
import type { CSSProperties, ReactNode } from "react";
import { CalendarClock, FolderPlus, ListTodo, Mail, Phone, Plug, Sparkles, Users } from "lucide-react";
import type { Proposal, ProposalKind } from "./proposals";
import { surface, type as ty } from "./tokens";

const chipS: CSSProperties = {
  ...ty.chip, color: "var(--t2)", background: surface.raised, border: "1px solid var(--line)",
  borderRadius: 999, padding: "6px 13px", cursor: "pointer", lineHeight: 1.4, textAlign: "left",
  maxWidth: "100%", display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0,
  transition: "background .12s, color .12s, border-color .12s",
};

/** The dismissable pair: the act, then the `×`. One border around both so it still reads as one
 *  pill, and the act keeps the full radius on its left so nothing looks clipped. */
const pairS: CSSProperties = {
  display: "inline-flex", alignItems: "stretch", maxWidth: "100%",
  border: "1px solid var(--line)", borderRadius: 999, background: surface.raised,
};
const inPairS: CSSProperties = { ...chipS, border: "none", background: "transparent", paddingRight: 6 };
const dismissS: CSSProperties = {
  ...ty.chip, color: "var(--t3)", background: "transparent", border: "none", cursor: "pointer",
  padding: "6px 11px 6px 4px", borderRadius: 999, lineHeight: 1.4,
};
/** Where a jtbd chip says what it came from. Dim, inside the same button — one click target, and
 *  the source is context for the act rather than a second thing to read. */
const sourceS: CSSProperties = { color: "var(--t3)", marginLeft: 6 };
/** The label: the part a narrow row cuts, with an ellipsis. Layout only. */
const labelS: CSSProperties = { minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };

const ICON = { size: 14, strokeWidth: 1.75 } as const;
const KIND_ICON: Record<ProposalKind, ReactNode> = {
  prep: <CalendarClock {...ICON} />,
  "check-email": <Mail {...ICON} />,
  connect: <Plug {...ICON} />,
  call: <Phone {...ICON} />,
  "shared-workspace": <Users {...ICON} />,
  jtbd: <ListTodo {...ICON} />,
  setup: <FolderPlus {...ICON} />,
  home: <Sparkles {...ICON} />,
};

function hoverIn(el: HTMLElement) {
  el.style.background = surface.raisedHi; el.style.color = "var(--t1)"; el.style.borderColor = "var(--line2)";
}
function hoverOut(el: HTMLElement) {
  el.style.background = surface.raised; el.style.color = "var(--t2)"; el.style.borderColor = "var(--line)";
}

export function ProposalChips(
  { items, onPick, onDismiss }: {
    items: Proposal[];
    onPick: (p: Proposal) => void;
    onDismiss?: (p: Proposal) => void;
  },
) {
  if (!items.length) return null;
  return (
    <div data-proposals role="group" aria-label="Suggestions"
      style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 20 }}>
      {items.map((p) => {
        const body = (
          <>
            <span className="vx-chip-icon" aria-hidden="true">{KIND_ICON[p.kind]}</span>
            <span data-proposal-label style={labelS}>
              {p.label}
              {p.source ? <span style={sourceS}>· {p.source}</span> : null}
            </span>
          </>
        );
        if (p.itemId && onDismiss) {
          return (
            <span key={p.id} style={pairS}>
              <button data-proposal={p.kind} type="button" style={inPairS} title={p.label} onClick={() => onPick(p)}>
                {body}
              </button>
              <button data-dismiss={p.itemId} type="button" style={dismissS}
                aria-label={`Dismiss: ${p.label}`} title="Not now"
                onClick={() => onDismiss(p)}>×</button>
            </span>
          );
        }
        return (
          <button key={p.id} data-proposal={p.kind} type="button" style={chipS} title={p.label} onClick={() => onPick(p)}
            onMouseEnter={(e) => hoverIn(e.currentTarget)}
            onMouseLeave={(e) => hoverOut(e.currentTarget)}
            onFocus={(e) => hoverIn(e.currentTarget)}
            onBlur={(e) => hoverOut(e.currentTarget)}>
            {body}
          </button>
        );
      })}
    </div>
  );
}

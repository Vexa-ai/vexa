"use client";
/** The proposal chips — one row, in the void an empty chat leaves between its greeting and the
 *  composer. Presentational only: what to offer is `proposals()`, what a click does is the shell's.
 *
 *  A click FIRES (founder ruling, consistency with the emailed links): the turn goes, with nothing
 *  left in the composer to press Enter on. And it fires IN THIS CHAT — a chip never opens a second
 *  conversation (founder, 2026-09-01: "this chat is already new"). The row is passed empty once a
 *  chip has been pressed, so the offer cannot be taken twice while its turn is still settling.
 *
 *  A CHIP WRITTEN BY AN AGENT SAYS WHERE IT CAME FROM, AND CAN BE REFUSED (Vexa-ai/vexa#1614). The
 *  derived and standing chips are statements about the product; a `jtbd` chip is a job somebody
 *  else's turn decided you had, so it carries two things the others do not need: its SOURCE, in
 *  human words, rendered beside the act — and a `×`, because a list you cannot say no to stops
 *  being a list and becomes a nag. Dismissing does NOT spend the row: the item leaves, the rest of
 *  the offer stays exactly where it was. */
import type { CSSProperties } from "react";
import type { Proposal } from "./proposals";
import { surface, type as ty } from "./tokens";

const chipS: CSSProperties = {
  ...ty.chip, color: "var(--t2)", background: surface.raised, border: "1px solid var(--line)",
  borderRadius: 999, padding: "6px 13px", cursor: "pointer", lineHeight: 1.4, textAlign: "left",
  maxWidth: "100%", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
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
            {p.label}
            {p.source ? <span style={sourceS}>· {p.source}</span> : null}
          </>
        );
        if (p.itemId && onDismiss) {
          return (
            <span key={p.id} style={pairS}>
              <button data-proposal={p.kind} type="button" style={inPairS} onClick={() => onPick(p)}>
                {body}
              </button>
              <button data-dismiss={p.itemId} type="button" style={dismissS}
                aria-label={`Dismiss: ${p.label}`} title="Not now"
                onClick={() => onDismiss(p)}>×</button>
            </span>
          );
        }
        return (
          <button key={p.id} data-proposal={p.kind} type="button" style={chipS} onClick={() => onPick(p)}
            onMouseEnter={(e) => hoverIn(e.currentTarget)}
            onMouseLeave={(e) => hoverOut(e.currentTarget)}>
            {body}
          </button>
        );
      })}
    </div>
  );
}

"use client";
/** The proposal chips — one row, in the void an empty chat leaves between its greeting and the
 *  composer. Presentational only: what to offer is `proposals()`, what a click does is the shell's.
 *
 *  A click FIRES (founder ruling, consistency with the emailed links): the chat opens and the turn
 *  goes, with nothing left in the composer to press Enter on. */
import type { CSSProperties } from "react";
import type { Proposal } from "./proposals";
import { surface, type as ty } from "./tokens";

const chipS: CSSProperties = {
  ...ty.chip, color: "var(--t2)", background: surface.raised, border: "1px solid var(--line)",
  borderRadius: 999, padding: "6px 13px", cursor: "pointer", lineHeight: 1.4, textAlign: "left",
  maxWidth: "100%", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
  transition: "background .12s, color .12s, border-color .12s",
};

export function ProposalChips({ items, onPick }: { items: Proposal[]; onPick: (p: Proposal) => void }) {
  if (!items.length) return null;
  return (
    <div data-proposals role="group" aria-label="Suggestions"
      style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 20 }}>
      {items.map((p) => (
        <button key={p.id} data-proposal={p.kind} type="button" style={chipS} onClick={() => onPick(p)}
          onMouseEnter={(e) => { e.currentTarget.style.background = surface.raisedHi; e.currentTarget.style.color = "var(--t1)"; e.currentTarget.style.borderColor = "var(--line2)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = surface.raised; e.currentTarget.style.color = "var(--t2)"; e.currentTarget.style.borderColor = "var(--line)"; }}>
          {p.label}
        </button>
      ))}
    </div>
  );
}

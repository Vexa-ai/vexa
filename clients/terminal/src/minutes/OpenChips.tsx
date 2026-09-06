"use client";
/** The open chips — one row above the composer, in every meeting chat (Vexa-ai/vexa#1586).
 *
 *  Presentational only, exactly as `ProposalChips` is: what to offer is `openChips()` (pure, in
 *  `openChips.ts`), what a click does is the shell's `openPage`. The difference from the proposal
 *  row is where it lives — this one does not go away when the conversation starts, because the
 *  transcript does not go away when the conversation starts.
 *
 *  A click OPENS. It sends no turn, spends no model call and writes nothing: the page is already in
 *  the chat's record and this only puts it in front. */
import type { CSSProperties } from "react";
import type { OpenChip } from "./openChips";
import { surface, type as ty } from "./tokens";

const chipS: CSSProperties = {
  ...ty.chip, color: "var(--t2)", background: surface.raised, border: "1px solid var(--line)",
  borderRadius: 999, padding: "4px 12px", cursor: "pointer", lineHeight: 1.4, textAlign: "left",
  maxWidth: "100%", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
  transition: "background .12s, color .12s, border-color .12s",
};

export function OpenChips({ items, onPick }: { items: OpenChip[]; onPick: (c: OpenChip) => void }) {
  if (!items.length) return null;
  return (
    <div data-open-chips role="group" aria-label="Open"
      style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
      {items.map((c) => (
        <button key={c.id} data-open-chip={c.id} type="button" style={chipS} onClick={() => onPick(c)}
          onMouseEnter={(e) => { e.currentTarget.style.background = surface.raisedHi; e.currentTarget.style.color = "var(--t1)"; e.currentTarget.style.borderColor = "var(--line2)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = surface.raised; e.currentTarget.style.color = "var(--t2)"; e.currentTarget.style.borderColor = "var(--line)"; }}>
          {c.label}
        </button>
      ))}
    </div>
  );
}

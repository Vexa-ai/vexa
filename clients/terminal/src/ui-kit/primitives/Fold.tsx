"use client";
/** Fold (guidelines §3.3) — show one form of a control at or above a PANE width and another below
 *  it, decided by a container query on the nearest pane (`.vx-pane`), never by the viewport.
 *
 *  Both forms are rendered and CSS hides one (`display: none`, so the hidden form is out of the
 *  accessibility tree and the tab order too). That keeps the switch instant on resize and free of
 *  measuring. Breakpoints are a fixed set (container queries cannot read a prop): 280, 360, 400,
 *  520 and 560 — the widths the guidelines name. */
import type { ReactNode } from "react";

export const FOLD_AT = [280, 360, 400, 520, 560] as const;
export type FoldAt = (typeof FOLD_AT)[number];

export function Fold({ at, wide, narrow, grow }: { at: FoldAt; wide: ReactNode; narrow: ReactNode; grow?: boolean }) {
  return (
    <span className="vx-fold" data-at={at} data-grow={grow ? "" : undefined}>
      <span className="vx-fold-wide">{wide}</span>
      <span className="vx-fold-narrow">{narrow}</span>
    </span>
  );
}

/** MINUTES design tokens — metrics, SURFACE DEPTH and the type scale in one place, so the three
 *  columns stay aligned and the whole shell speaks one typographic language.
 *
 *  Depth: the two rails RECEDE (they sit at the deepest surface, `--bg`) and the conversation
 *  LIFTS toward the reader (`--sidebar`). That reads as focus without inventing any new colour —
 *  every value below resolves from the terminal's own CSS variables, in both themes. */
import type { CSSProperties } from "react";

export const T = {
  // F61 (founder, 2026-09-02, screenshot of the minutes chat): *"left sidebar too wide"* — the rail
  // was taking ~440px of a 2000px window. This REVERSES the +60% widening of 2026-09-01 (248 → 397),
  // which was measured against a name that no longer needs it: rows truncate with an ellipsis
  // (Rail.tsx), so width buys a few more characters and costs the chat and the panel real room.
  // The `<` control still folds it to an icon strip, and that choice persists.
  railW: 240,
  pagesMin: 240,     // was 300; safe since 3875079b6 made the pages chips ellipsize below 364
  pagesMax: 1600,    // an absolute ceiling only — the real limit is the viewport fraction below
  pagesFrac: 0.6,    // "read a transcript wide" (founder, 2026-09-01): 60% of the viewport
  chatMin: 280,      // …but the conversation never vanishes behind the document
  pagesDefault: 384,
  headerH: 46,
  rowPadX: 8,
} as const;

/** The widest the pages panel may go on a given viewport. 60% of it — unless that would squeeze the
 *  conversation below `chatMin`, which on a 1440px screen it does: there the cap lands near 53%.
 *  Both bounds are real, and this is the one place they meet, so the drag, the arrow keys and the
 *  stored width cannot disagree about them. */
export const maxPagesW = (vw: number): number =>
  Math.max(T.pagesMin, Math.min(T.pagesMax, Math.round(vw * T.pagesFrac), vw - T.railW - T.chatMin));

/** Surfaces — one ladder, deepest first. */
export const surface = {
  rail: "var(--bg)",          // recedes
  pages: "var(--bg)",         // recedes
  center: "var(--sidebar)",   // lifts — the focal plane
  headStrip: "color-mix(in srgb, var(--sidebar) 55%, transparent)",
  raised: "var(--panel)",     // selected rows, chips
  raisedHi: "var(--panel2)",  // hover / pressed
} as const;

/** ONE type scale. Every size/weight in the shell comes from here — never a literal. */
export const type = {
  title: { fontFamily: "var(--sans)", fontSize: 14, fontWeight: 600, letterSpacing: "-0.005em" } as CSSProperties,
  body: { fontFamily: "var(--sans)", fontSize: 13, fontWeight: 400 } as CSSProperties,
  bodyStrong: { fontFamily: "var(--sans)", fontSize: 13, fontWeight: 600 } as CSSProperties,
  meta: { fontFamily: "var(--sans)", fontSize: 11, fontWeight: 400, color: "var(--t3)" } as CSSProperties,
  lens: { fontFamily: "var(--sans)", fontSize: 10.5, fontWeight: 600, letterSpacing: ".09em", textTransform: "uppercase", color: "var(--t3)" } as CSSProperties,
  pill: { fontFamily: "var(--sans)", fontSize: 10.5, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase" } as CSSProperties,
  mono: { fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--t3)" } as CSSProperties,
  control: { fontFamily: "var(--sans)", fontSize: 12.5, fontWeight: 600 } as CSSProperties,
  chip: { fontFamily: "var(--sans)", fontSize: 12, fontWeight: 500 } as CSSProperties,
} as const;

export const row = {
  base: (on: boolean): CSSProperties => ({
    ...type.body,
    display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left",
    padding: "5px 8px", borderRadius: 7, cursor: "pointer", border: "none",
    color: on ? "var(--t1)" : "var(--t2)", background: on ? surface.raised : "transparent",
  }),
  dot: (on: boolean): CSSProperties => ({ width: 5, height: 5, borderRadius: "50%", flex: "none", background: on ? "var(--accent)" : "var(--line2)" }),
  ghostPlus: { marginLeft: "auto", background: "transparent", border: "none", color: "var(--t3)", fontSize: 15, lineHeight: 1, cursor: "pointer", padding: "0 2px", fontFamily: "var(--sans)" } as CSSProperties,
};

/** The shared header band — same height and treatment in all three columns. */
export const header: CSSProperties = {
  height: T.headerH, flex: "none", display: "flex", alignItems: "center", gap: 10,
  padding: "0 16px", borderBottom: "1px solid var(--line)", background: surface.headStrip,
};

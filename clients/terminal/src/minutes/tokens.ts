/** MINUTES design tokens — one place for the shell's metrics so the three columns stay aligned.
 *  Colors come from the terminal's CSS variables (globals.css); the shell adds NO new colors. */
import type { CSSProperties } from "react";

export const T = {
  railW: 248,
  pagesW: 384,
  headerH: 46,           // ONE header row across rail · context bar · pages head — the alignment line
  rowPadX: 8,
} as const;

export const text = {
  lens: { fontSize: 10.5, letterSpacing: ".09em", textTransform: "uppercase", color: "var(--t3)", fontWeight: 600 } as CSSProperties,
  sub: { fontSize: 10.5, color: "var(--t3)" } as CSSProperties,
  mono: { fontFamily: "ui-monospace, monospace", fontSize: 11.5, color: "var(--t3)" } as CSSProperties,
};

export const row = {
  base: (on: boolean): CSSProperties => ({
    display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left", fontFamily: "inherit",
    padding: "5px 8px", borderRadius: 7, fontSize: 13, cursor: "pointer", border: "none",
    color: on ? "var(--t1)" : "var(--t2)", background: on ? "var(--panel2)" : "transparent",
  }),
  dot: (on: boolean): CSSProperties => ({ width: 5, height: 5, borderRadius: "50%", flex: "none", background: on ? "var(--accent)" : "var(--line2)" }),
  ghostPlus: { marginLeft: "auto", background: "transparent", border: "none", color: "var(--t3)", fontSize: 15, lineHeight: 1, cursor: "pointer", padding: "0 2px" } as CSSProperties,
};

export const header: CSSProperties = {
  height: T.headerH, flex: "none", display: "flex", alignItems: "center", gap: 10,
  padding: "0 16px", borderBottom: "1px solid var(--line2)", background: "var(--sidebar)",
};

"use client";
/** Both side columns fold away — the chat list on the left, the pages panel on the right.
 *
 *  A three-column shell on a laptop leaves the conversation about 600px, and a transcript read at
 *  60% of the viewport leaves it less. Either column can go, independently, and the centre takes
 *  the space; the choice persists per side, so the reader sets their shape once. WHAT FITS is no
 *  longer this file's question: `shellLayout()` (ui-kit/layout) folds the columns on its own as the
 *  window narrows, without touching the reader's choice.
 *
 *  COLLAPSED MEANS COLLAPSED: what is left of the pages panel is one chevron at the edge, not a stub
 *  that keeps its title. (The rail folds to its icon strip, `RailStrip`.)
 *
 *  Icons are lucide, not text glyphs; every control here is at least 24×24 (WCAG 2.5.8). */
import type { CSSProperties } from "react";
import { ChevronLeft, ChevronRight, FileText, Menu as MenuIcon } from "lucide-react";
import { EDGE_W } from "../ui-kit";
import { T, surface } from "./tokens";

const chevron: CSSProperties = {
  flex: "none", width: 24, height: 24, display: "flex", alignItems: "center", justifyContent: "center",
  background: "transparent", border: "none", borderRadius: 6, color: "var(--t3)", cursor: "pointer", padding: 0,
  transition: "color .12s, background .12s",
};
const lit = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.color = "var(--t1)"; e.currentTarget.style.background = surface.raised; };
const dim = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.color = "var(--t3)"; e.currentTarget.style.background = "transparent"; };

const NAME = { left: "chat list", right: "pages panel" } as const;

/** The chevron an OPEN column carries in its own header. It points AT the edge it folds toward, so
 *  the direction reads as "put this away" rather than as a navigation. `label` overrides the name
 *  where folding is really closing an overlay ("Back to the conversation"). */
export function CollapseButton({ side, onClick, label }: { side: "left" | "right"; onClick: () => void; label?: string }) {
  const name = label ?? `Hide the ${NAME[side]}`;
  return (
    <button data-collapse={side} aria-label={name} title={name} onClick={onClick}
      style={chevron} onMouseEnter={lit} onMouseLeave={dim}>
      {side === "left" ? <ChevronLeft size={16} strokeWidth={1.75} aria-hidden /> : <ChevronRight size={16} strokeWidth={1.75} aria-hidden />}
    </button>
  );
}

/** What a collapsed pages panel leaves at the edge: the reopen handle, and nothing else. It sits in
 *  the same grid cell the column had, so the layout stays one grid with three columns. */
export function EdgeHandle({ side, onClick }: { side: "left" | "right"; onClick: () => void }) {
  const label = `Show the ${NAME[side]}`;
  const base: CSSProperties = {
    gridRow: "1 / 3", background: surface.rail, display: "flex", flexDirection: "column", width: EDGE_W,
    alignItems: "center", minWidth: 0, overflow: "hidden",
  };
  return (
    <div data-edge={side} style={side === "left"
      ? { ...base, gridColumn: 1, borderRight: "1px solid var(--line)" }
      : { ...base, gridColumn: 3, borderLeft: "1px solid var(--line)" }}>
      <div style={{ height: T.headerH, flex: "none", display: "flex", alignItems: "center", borderBottom: "1px solid var(--line)", width: "100%", justifyContent: "center" }}>
        <button data-expand={side} aria-label={label} title={label} onClick={onClick}
          style={chevron} onMouseEnter={lit} onMouseLeave={dim}>
          {side === "left" ? <ChevronRight size={16} strokeWidth={1.75} aria-hidden /> : <ChevronLeft size={16} strokeWidth={1.75} aria-hidden />}
        </button>
      </div>
    </div>
  );
}

/** The header's pane controls where a pane is an OVERLAY (narrow and single modes): the chat list
 *  (single mode — there is no strip to open it from) and the pages sheet. `aria-expanded` says
 *  whether the overlay is open. */
export function ShellToggle({ kind, open, onClick }: { kind: "rail" | "pages"; open: boolean; onClick: () => void }) {
  const label = kind === "rail" ? (open ? "Close the chat list" : "Open the chat list") : (open ? "Close the pages panel" : "Open the pages panel");
  return (
    <button data-shell-toggle={kind} aria-label={label} title={label} aria-expanded={open} onClick={onClick}
      style={{ ...chevron, width: 28, height: 28, color: open ? "var(--t1)" : "var(--t2)", background: open ? surface.raised : "transparent" }}>
      {kind === "rail" ? <MenuIcon size={16} strokeWidth={1.75} aria-hidden /> : <FileText size={16} strokeWidth={1.75} aria-hidden />}
    </button>
  );
}

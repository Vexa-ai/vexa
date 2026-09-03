"use client";
/** Both side columns fold away — the chat list on the left, the pages panel on the right.
 *
 *  A three-column shell on a laptop leaves the conversation about 600px, and a transcript read at
 *  60% of the viewport leaves it less. Either column can go, independently, and the centre takes
 *  the space; the choice persists per side, so the reader sets their shape once.
 *
 *  COLLAPSED MEANS COLLAPSED: what is left is a hairline and one chevron, not a stub that keeps its
 *  title. A column that hides its contents but keeps a 60px shoulder has not given the space back,
 *  which was the whole request.
 *
 *  Collapse and the pages panel's drag-resize coexist without knowing about each other: collapsing
 *  never writes the width, so reopening restores the width that was there. Collapse simply wins
 *  while it is on. */
import type { CSSProperties } from "react";
import { T, surface } from "./tokens";

/** All a collapsed column keeps — the width of one chevron, matching the panel's nav buttons. */
export const EDGE_W = 22;

const chevron: CSSProperties = {
  flex: "none", width: 20, height: 24, display: "flex", alignItems: "center", justifyContent: "center",
  background: "transparent", border: "none", borderRadius: 6, fontFamily: "var(--sans)", fontSize: 15,
  lineHeight: 1, color: "var(--t3)", cursor: "pointer", padding: 0, transition: "color .12s, background .12s",
};
const lit = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.color = "var(--t1)"; e.currentTarget.style.background = surface.raised; };
const dim = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.color = "var(--t3)"; e.currentTarget.style.background = "transparent"; };

const NAME = { left: "chat list", right: "pages panel" } as const;

/** The chevron an OPEN column carries in its own header. It points AT the edge it folds toward, so
 *  the direction reads as "put this away" rather than as a navigation. */
export function CollapseButton({ side, onClick }: { side: "left" | "right"; onClick: () => void }) {
  const label = `Hide the ${NAME[side]}`;
  return (
    <button data-collapse={side} aria-label={label} title={label} onClick={onClick}
      style={chevron} onMouseEnter={lit} onMouseLeave={dim}>
      {side === "left" ? "‹" : "›"}
    </button>
  );
}

/** What a collapsed column leaves at the edge: the reopen handle, and nothing else. It sits in the
 *  same grid cell the column had, so the layout stays one grid with three columns — only the width
 *  of this one changed. */
export function EdgeHandle({ side, onClick }: { side: "left" | "right"; onClick: () => void }) {
  const label = `Show the ${NAME[side]}`;
  const base: CSSProperties = {
    gridRow: "1 / 3", background: surface.rail, display: "flex", flexDirection: "column",
    alignItems: "center", minWidth: 0, overflow: "hidden",
  };
  return (
    <div style={side === "left"
      ? { ...base, gridColumn: 1, borderRight: "1px solid var(--line)" }
      : { ...base, gridColumn: 3, borderLeft: "1px solid var(--line)" }}>
      <div style={{ height: T.headerH, flex: "none", display: "flex", alignItems: "center", borderBottom: "1px solid var(--line)", width: "100%", justifyContent: "center" }}>
        <button data-expand={side} aria-label={label} title={label} onClick={onClick}
          style={chevron} onMouseEnter={lit} onMouseLeave={dim}>
          {side === "left" ? "›" : "‹"}
        </button>
      </div>
    </div>
  );
}

"use client";
/** Entities — the knowledge graph surfaced as interactive cards.
 *  • EntityList (center): "in the room" + "detected" — click a card to open its workspace doc tab.
 *  • Research actions run in the active meeting copilot via a tiny bus. */
import { useState, type CSSProperties, type ReactNode } from "react";
import type { TabDescriptor } from "../workbench/layout";
import { Icon } from "../ui-kit";
import { type Entity, type EntityType } from "./meetingModel";

// ── type → visual language ───────────────────────────────────────────────────────
const TYPE: Record<EntityType, { icon: string; color: string; bg: string; label: string }> = {
  person: { icon: "user", color: "var(--blue)", bg: "var(--bluebg)", label: "Person" },
  company: { icon: "building", color: "var(--accent)", bg: "var(--accentbg)", label: "Company" },
  topic: { icon: "tag", color: "var(--violet)", bg: "var(--violetbg)", label: "Topic" },
  task: { icon: "tasks", color: "var(--green)", bg: "var(--greenbg)", label: "Task" },
};
const initials = (name: string) => name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

function Tile({ e, size = 28 }: { e: Entity; size?: number }) {
  const t = TYPE[e.type];
  const round = e.type === "person";
  return (
    <span style={{ width: size, height: size, flex: "none", borderRadius: round ? "50%" : Math.round(size * 0.28), background: t.bg, color: t.color, display: "flex", alignItems: "center", justifyContent: "center", fontSize: Math.round(size * 0.4), fontWeight: 600, letterSpacing: e.type === "person" ? ".02em" : 0 }}>
      {e.type === "person" ? initials(e.title) : <Icon name={t.icon} size={Math.round(size * 0.52)} />}
    </span>
  );
}

// ── research bus — the active chat registers a handler; list + card emit into it ──
type ResearchFn = (title: string) => void;
let _research: ResearchFn | null = null;
export const onResearchRequest = (fn: ResearchFn): (() => void) => { _research = fn; return () => { if (_research === fn) _research = null; }; };

// ── navigation helpers ───────────────────────────────────────────────────────────
export const entityDocTab = (e: Entity): TabDescriptor => ({ id: `doc:${e.path}`, title: e.title, kind: "doc", params: { path: e.path } });

// ── one entity card-row (center list) — a quiet, dense list item ───────────────────
//  At rest: no border, no fill — just the tile, a tight name/subtitle, and a faint
//  type-color presence dot. On hover: a soft wash, a 2px type-color rail on the left
//  edge, and a single icon-button to research. The chevron is gone.
function EntityRow({ e, onOpen, onResearch }: { e: Entity; onOpen: (e: Entity) => void; onResearch: (e: Entity) => void }) {
  const [hover, setHover] = useState(false);
  const t = TYPE[e.type];
  const row: CSSProperties = {
    position: "relative", display: "flex", alignItems: "center", gap: 11, width: "100%", textAlign: "left",
    padding: "7px 8px 7px 11px", borderRadius: 9, cursor: "pointer",
    background: hover ? "var(--panel)" : "transparent", transition: "background .12s ease",
  };
  return (
    <div className="vx-row" style={row} onClick={() => onOpen(e)} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <span className="vx-row-rail" style={{ position: "absolute", left: 3, top: 8, bottom: 8, width: 2, borderRadius: 2, background: t.color }} />
      <Tile e={e} />
      <div style={{ minWidth: 0, flex: 1, display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontSize: 13, color: "var(--t1)", fontWeight: 550, lineHeight: 1.25, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flex: "0 1 auto" }}>{e.title}</span>
        <span style={{ fontSize: 11.5, color: "var(--t3)", lineHeight: 1.25, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flex: "1 1 auto" }}>{e.subtitle}</span>
      </div>
      {!e.exists && (
        <span title="not yet in your workspace" style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 10, fontWeight: 600, color: t.color, letterSpacing: ".04em", textTransform: "uppercase", flex: "none" }}>
          <span style={{ width: 5, height: 5, borderRadius: "50%", background: t.color }} />new
        </span>
      )}
      <button className="vx-row-act" onClick={(ev) => { ev.stopPropagation(); onResearch(e); }} title={`Research ${e.title}`} aria-label={`Research ${e.title}`}
        style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 26, height: 26, border: "1px solid var(--line2)", borderRadius: 7, background: "var(--panel2)", color: "var(--accent)", cursor: "pointer", flex: "none" }}>
        <Icon name="spark" size={14} />
      </button>
    </div>
  );
}

function Section({ label, count, children }: { label: string; count: number; children: ReactNode }) {
  return (
    <div style={{ marginBottom: 22 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 8px 6px" }}>
        <span style={{ fontSize: 10.5, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".07em", fontWeight: 600 }}>{label}</span>
        <span style={{ fontSize: 10.5, color: "var(--t3)", fontFamily: "var(--mono)", lineHeight: 1 }}>{count}</span>
        <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>{children}</div>
    </div>
  );
}

export function EntityList({ present, detected, onOpen, onResearch }: { present: Entity[]; detected: Entity[]; onOpen: (e: Entity) => void; onResearch: (e: Entity) => void }) {
  return (
    <div>
      {present.length > 0 && <Section label="In the room" count={present.length}>{present.map((e) => <EntityRow key={e.title} e={e} onOpen={onOpen} onResearch={onResearch} />)}</Section>}
      {detected.length > 0 && <Section label="Detected" count={detected.length}>{detected.map((e) => <EntityRow key={e.title} e={e} onOpen={onOpen} onResearch={onResearch} />)}</Section>}
    </div>
  );
}

"use client";
/** Entities — the knowledge graph surfaced as interactive cards.
 *  • EntityList (center): "in the room" + "detected" — click a card to open its entity card in the
 *    right sidebar (the agent creates it first if it doesn't exist yet); a Research action runs in chat.
 *  • EntityCard (right context "entity"): the entity's facts · summary · related links · open-full /
 *    research actions. Resolves create-if-missing.
 *  Research is decoupled via a tiny bus so both the list and the card can start it in the active chat. */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { registerContext, type ContextProps } from "../contributions";
import { Icon } from "../ui-kit";
import { entityFor, type Entity, type EntityType } from "./mock";

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
export const requestResearch = (title: string) => _research?.(title);

// ── navigation helpers ───────────────────────────────────────────────────────────
export const entityDocTab = (e: Entity): TabDescriptor => ({ id: `doc:${e.path}`, title: e.title, kind: "doc", params: { path: e.path }, context: { kind: "doc-context", params: { path: e.path } } });

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

// ── EntityCard (right context "entity") — create-if-missing, then the card ────────
function linkify(text: string, onLink: (title: string) => void): ReactNode[] {
  return text.split(/(\[\[[^\]]+\]\])/).map((p, i) => {
    const m = p.match(/^\[\[(.+)\]\]$/);
    return m ? <span key={i} onClick={() => onLink(m[1])} style={{ color: "var(--blue)", cursor: "pointer" }}>{p}</span> : <span key={i}>{p}</span>;
  });
}

function EntityCard({ params }: ContextProps) {
  const layout = useService(LayoutServiceId);
  const title = params.title as string;
  const from = params.from as string | undefined;
  const e = entityFor(title);
  const t = TYPE[e.type];
  const [creating, setCreating] = useState(!e.exists);
  useEffect(() => { setCreating(!e.exists); if (!e.exists) { const id = setTimeout(() => setCreating(false), 900); return () => clearTimeout(id); } }, [title, e.exists]);
  const openEntity = (name: string) => layout.setContext({ kind: "entity", params: { title: name, from } });

  return (
    <div style={{ padding: "18px 16px 24px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 11, marginBottom: 18 }}>
        <Tile e={e} size={36} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 15, color: "var(--t1)", fontWeight: 600, lineHeight: 1.25, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{e.title}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
            <span style={{ fontSize: 11, color: t.color, fontWeight: 550 }}>{t.label}</span>
            {!e.exists && !creating && <><span style={{ color: "var(--t3)" }}>·</span><span style={{ fontSize: 11, color: "var(--t3)" }}>created just now</span></>}
          </div>
        </div>
        {from && <button onClick={() => layout.setContext({ kind: "transcript", params: { meetingId: from } })} title="Back to transcript" aria-label="Back to transcript" style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, background: "none", border: "1px solid var(--line2)", borderRadius: 7, color: "var(--t3)", cursor: "pointer", flex: "none" }}><Icon name="msg" size={14} /></button>}
      </div>

      {creating ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--t2)", fontSize: 12.5, padding: "11px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)" }}>
          <span className="vx-op-spin" style={{ width: 13, height: 13, flex: "none", borderRadius: "50%", border: "1.5px solid var(--line2)", borderTopColor: "var(--accent)" }} />
          <span>Creating <span style={{ color: "var(--blue)" }}>[[{e.title}]]</span> in your workspace…</span>
        </div>
      ) : (
        <>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)", marginBottom: 16 }}>
            <Icon name="file" size={12} style={{ color: "var(--t3)" }} /><span style={{ wordBreak: "break-all" }}>{e.path}</span>
          </div>
          {e.facts && e.facts.length > 0 && (
            <div style={{ border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)", padding: "4px 12px", marginBottom: 16 }}>
              {e.facts.map(([k, v], i) => (
                <div key={k} style={{ display: "flex", gap: 12, alignItems: "baseline", fontSize: 12.5, padding: "8px 0", borderTop: i === 0 ? "none" : "1px solid var(--line)" }}>
                  <span style={{ color: "var(--t3)", width: 72, flex: "none", textTransform: "uppercase", letterSpacing: ".04em", fontSize: 10.5, lineHeight: 1.5 }}>{k}</span>
                  <span style={{ color: "var(--t1)", lineHeight: 1.5 }}>{linkify(v, openEntity)}</span>
                </div>
              ))}
            </div>
          )}
          {e.summary && <div style={{ fontSize: 13, color: "var(--t2)", lineHeight: 1.6, marginBottom: 18 }}>{e.summary}</div>}
          {e.related && e.related.length > 0 && <>
            <div style={{ fontSize: 10.5, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".07em", fontWeight: 600, marginBottom: 9 }}>Related</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 20 }}>
              {e.related.map((r) => (
                <span key={r} onClick={() => openEntity(r)} title={`Open ${r}`}
                  style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--t2)", border: "1px solid var(--line2)", borderRadius: 7, padding: "3px 9px", cursor: "pointer" }}
                  onMouseEnter={(ev) => { ev.currentTarget.style.color = "var(--t1)"; ev.currentTarget.style.background = "var(--panel2)"; }}
                  onMouseLeave={(ev) => { ev.currentTarget.style.color = "var(--t2)"; ev.currentTarget.style.background = "transparent"; }}>
                  <Icon name="link" size={12} style={{ color: "var(--blue)" }} />{r}
                </span>
              ))}
            </div>
          </>}
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => requestResearch(e.title)} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6, flex: 1, border: "none", borderRadius: 8, background: "var(--accent)", color: "#241008", padding: "8px 12px", fontSize: 12.5, cursor: "pointer", fontWeight: 600 }}><Icon name="spark" size={14} />Research</button>
            <button onClick={() => layout.openTab(entityDocTab(e))} title="Open full page" aria-label="Open full page" style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6, border: "1px solid var(--line2)", borderRadius: 8, background: "var(--panel)", color: "var(--t1)", padding: "8px 12px", fontSize: 12.5, cursor: "pointer", flex: "none" }}><Icon name="openIn" size={14} /></button>
          </div>
        </>
      )}
    </div>
  );
}

registerContext("entity", EntityCard);

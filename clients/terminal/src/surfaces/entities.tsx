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

function Tile({ e, size = 34 }: { e: Entity; size?: number }) {
  const t = TYPE[e.type];
  const round = e.type === "person";
  return (
    <span style={{ width: size, height: size, flex: "none", borderRadius: round ? "50%" : 9, background: t.bg, color: t.color, display: "flex", alignItems: "center", justifyContent: "center", fontSize: size * 0.36, fontWeight: 500 }}>
      {e.type === "person" ? initials(e.title) : <Icon name={t.icon} size={size * 0.5} />}
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

// ── one entity card-row (center list) ─────────────────────────────────────────────
function EntityRow({ e, onOpen, onResearch }: { e: Entity; onOpen: (e: Entity) => void; onResearch: (e: Entity) => void }) {
  const [hover, setHover] = useState(false);
  const t = TYPE[e.type];
  const card: CSSProperties = { display: "flex", alignItems: "center", gap: 12, width: "100%", textAlign: "left", padding: "11px 12px", borderRadius: 12, cursor: "pointer", background: hover ? "var(--panel2)" : "var(--panel)", border: `1px solid ${hover ? "var(--line2)" : "var(--line)"}`, transition: "background .12s, border-color .12s" };
  return (
    <div style={card} onClick={() => onOpen(e)} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <Tile e={e} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 14.5, color: "var(--t1)", fontWeight: 500, lineHeight: 1.2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{e.title}</div>
        <div style={{ fontSize: 12, color: "var(--t3)", marginTop: 2 }}>{e.subtitle}</div>
      </div>
      {!e.exists && <span style={{ fontSize: 10.5, color: t.color, background: t.bg, borderRadius: 6, padding: "2px 7px", flex: "none" }}>new</span>}
      <button onClick={(ev) => { ev.stopPropagation(); onResearch(e); }} title={`Research ${e.title}`}
        style={{ display: "inline-flex", alignItems: "center", gap: 5, border: "1px solid var(--line2)", borderRadius: 8, background: hover ? "var(--panel)" : "transparent", color: hover ? "var(--t1)" : "var(--t2)", padding: "5px 9px", fontSize: 12, cursor: "pointer", flex: "none", transition: "color .12s" }}>
        <Icon name="spark" size={13} style={{ color: "var(--accent)" }} />Research
      </button>
      <Icon name="chevR" size={15} style={{ color: hover ? "var(--t2)" : "var(--t3)", flex: "none" }} />
    </div>
  );
}

function Section({ label, count, children }: { label: string; count: number; children: ReactNode }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".05em", margin: "0 2px 9px" }}>
        {label}<span style={{ color: "var(--t2)", background: "var(--panel2)", borderRadius: 20, padding: "0 7px", fontSize: 10.5, lineHeight: "16px" }}>{count}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{children}</div>
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
    <div style={{ padding: "16px 16px 22px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
        <Tile e={e} size={38} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 16, color: "var(--t1)", fontWeight: 500, lineHeight: 1.2 }}>{e.title}</div>
          <div style={{ fontSize: 11.5, color: t.color }}>{t.label}{!e.exists && !creating ? " · created just now" : ""}</div>
        </div>
        {from && <button onClick={() => layout.setContext({ kind: "transcript", params: { meetingId: from } })} title="Back to transcript" style={{ background: "none", border: "1px solid var(--line2)", borderRadius: 7, color: "var(--t3)", cursor: "pointer", fontSize: 11, padding: "3px 8px", flex: "none" }}>transcript</button>}
      </div>

      {creating ? (
        <div style={{ display: "flex", alignItems: "center", gap: 9, color: "var(--t2)", fontSize: 13, padding: "8px 2px" }}>
          <span className="vx-op-spin" style={{ width: 13, height: 13, borderRadius: "50%", border: "1.5px solid var(--line2)", borderTopColor: "var(--accent)" }} />
          Agent is creating <span style={{ color: "var(--blue)" }}>[[{e.title}]]</span> in your workspace…
        </div>
      ) : (
        <>
          <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)", marginBottom: 12 }}>{e.path}</div>
          {e.facts && e.facts.length > 0 && (
            <div style={{ border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)", padding: "10px 12px", marginBottom: 14 }}>
              {e.facts.map(([k, v]) => <div key={k} style={{ display: "flex", gap: 10, fontSize: 12.5, padding: "2px 0" }}><span style={{ color: "var(--t3)", width: 78, flex: "none" }}>{k}</span><span style={{ color: "var(--t1)" }}>{linkify(v, openEntity)}</span></div>)}
            </div>
          )}
          {e.summary && <div style={{ fontSize: 13, color: "var(--t2)", lineHeight: 1.6, marginBottom: 14 }}>{e.summary}</div>}
          {e.related && e.related.length > 0 && <>
            <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 8 }}>related</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 16 }}>
              {e.related.map((r) => <span key={r} onClick={() => openEntity(r)} style={{ fontSize: 12, color: "var(--blue)", border: "1px solid var(--line2)", borderRadius: 20, padding: "3px 10px", cursor: "pointer" }}>{r}</span>)}
            </div>
          </>}
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => requestResearch(e.title)} style={{ display: "inline-flex", alignItems: "center", gap: 6, border: "none", borderRadius: 9, background: "var(--accent)", color: "#241008", padding: "7px 12px", fontSize: 12.5, cursor: "pointer", fontWeight: 500 }}><Icon name="spark" size={14} />Research</button>
            <button onClick={() => layout.openTab(entityDocTab(e))} style={{ display: "inline-flex", alignItems: "center", gap: 6, border: "1px solid var(--line2)", borderRadius: 9, background: "var(--panel)", color: "var(--t1)", padding: "7px 12px", fontSize: 12.5, cursor: "pointer" }}><Icon name="openIn" size={13} />Open full page</button>
          </div>
        </>
      )}
    </div>
  );
}

registerContext("entity", EntityCard);

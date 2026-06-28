"use client";
/** The ONE live-transcript render engine (P23: the terminal RENDERS, it does not re-derive). It paints a
 *  list of segments — CONFIRMED text is stable + append-only (consecutive same-speaker segments merge into
 *  one flowing block) and the in-flight PENDING text is a single dimmed "live" tail — so the body never
 *  flickers while the unconfirmed window re-forms. Fed RAW segments (transcript) or PROCESSED segments
 *  (the cleaned mirror, which also carry keyword `tags` to research) by exactly the same code; the toggle
 *  picks the source, not the engine.
 *
 *  PROCESSED v2 rendering: when `entities` is supplied, entity mentions are highlighted INLINE (colored
 *  by kind, clickable → Research · Open entity doc) and copilot `signals` render as small
 *  actionable badges under the relevant block. RAW mode passes neither, so it stays plain text. */

import { useEffect, useRef, useState } from "react";
import { splitTextIntoSpans, type SpanEntity } from "./inlineSpans";

export interface EngineTag { label: string; kind: string }
export interface EngineEntity { id?: string; label: string; kind: string; docPath?: string }
export interface EngineSignal { id: string; kind: string; label: string }
export interface EngineSegment { speaker?: string; text: string; tsMs?: number; id?: string; completed?: boolean; tags?: EngineTag[] }

export interface EngineActions {
  research?(entity: { id?: string; name: string; kind: string }): void;
  openEntityDoc?(entity: { id?: string; name: string; kind: string; docPath?: string }): void;
  onSignal?(signal: EngineSignal): void;
}

const TAG_HUE: Record<string, string> = { person: "#2563eb", company: "#7c3aed", product: "#0d9488", number: "#b45309" };
const SIGNAL_HUE: Record<string, string> = { decision: "#7c3aed", "action-item": "#0d9488", action: "#0d9488", question: "#2563eb", claim: "#b45309" };

function hueFor(kind: string): string {
  return TAG_HUE[kind] ?? "var(--t2)";
}

/** An inline entity mention: colored, keyboard-focusable, opens a small action menu on click/Enter. */
function EntityMention({ entity, actions }: { entity: SpanEntity; actions?: EngineActions }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const hue = hueFor(entity.kind);
  const arg = { id: entity.id, name: entity.label, kind: entity.kind, docPath: entity.docPath };

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const run = (fn?: (a: typeof arg) => void) => () => { setOpen(false); fn?.(arg); };

  return (
    <span ref={ref} style={{ position: "relative", display: "inline" }}>
      <span
        role="button"
        tabIndex={0}
        aria-haspopup="menu"
        aria-expanded={open}
        title={`${entity.kind}: ${entity.label}`}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen((v) => !v); } }}
        style={{
          color: hue, cursor: "pointer", borderBottom: `1px dotted ${hue}`,
          background: open ? "color-mix(in srgb, currentColor 12%, transparent)" : "transparent",
          borderRadius: 3, padding: "0 1px", outlineColor: hue,
        }}
      >
        {entity.label}
      </span>
      {open && (
        <span
          role="menu"
          style={{
            position: "absolute", top: "100%", left: 0, zIndex: 20, marginTop: 4, minWidth: 150,
            display: "flex", flexDirection: "column", background: "var(--bg)", border: "1px solid var(--line2)",
            borderRadius: 8, boxShadow: "0 6px 20px rgba(0,0,0,0.18)", padding: 4, fontSize: 12, fontWeight: 500,
          }}
        >
          <button type="button" role="menuitem" onClick={run(actions?.research)} style={menuItemStyle}>Research</button>
          <button type="button" role="menuitem" onClick={run(actions?.openEntityDoc)} style={menuItemStyle}>Open entity doc</button>
        </span>
      )}
    </span>
  );
}

const menuItemStyle: React.CSSProperties = {
  textAlign: "left", background: "transparent", border: "none", color: "var(--t1)",
  cursor: "pointer", padding: "5px 8px", borderRadius: 5, fontSize: 12, lineHeight: 1.3,
};

/** Render a block's text. With `entities` → inline highlights; without → plain text (raw mode). */
function BlockText({ text, entities, actions }: { text: string; entities?: EngineEntity[]; actions?: EngineActions }) {
  if (!entities || !entities.length) return <>{text}</>;
  const spans = splitTextIntoSpans(text, entities);
  return (
    <>
      {spans.map((span, i) =>
        span.entity
          ? <EntityMention key={`e${i}`} entity={span.entity} actions={actions} />
          : <span key={`t${i}`}>{span.text}</span>,
      )}
    </>
  );
}

export function LiveTranscriptEngine({
  segments,
  emptyLabel = "Waiting for transcript…",
  entities,
  signals,
  actions,
}: {
  segments: EngineSegment[];
  emptyLabel?: string;
  entities?: EngineEntity[];
  signals?: EngineSignal[];
  actions?: EngineActions;
}) {
  // Confirmed (completed !== false) = stable. Merge consecutive same-speaker confirmed segments into
  // flowing blocks; keyword tags accumulate per block. Pending (completed === false) = the live edge.
  const blocks: { speaker?: string; tsMs?: number; text: string; key: string; tags: EngineTag[] }[] = [];
  for (const s of segments) {
    if (s.completed === false) continue;
    const last = blocks[blocks.length - 1];
    if (last && last.speaker === s.speaker) { last.text += " " + s.text; if (s.tags) last.tags.push(...s.tags); }
    else blocks.push({ speaker: s.speaker, tsMs: s.tsMs, text: s.text, key: s.id ?? `b${blocks.length}`, tags: [...(s.tags ?? [])] });
  }
  const lastPending = [...segments].reverse().find((s) => s.completed === false);
  const live = (lastPending?.text ?? "").trim();
  const liveSpeaker = lastPending?.speaker;

  const lastBlock = blocks[blocks.length - 1];
  const liveJoinsLast = !!live && !!lastBlock && lastBlock.speaker === liveSpeaker;
  const liveOwnBlock = !!live && !liveJoinsLast;

  if (!blocks.length && !live) {
    return <div style={{ color: "var(--t3)", fontSize: 13, padding: "8px 2px" }}>{emptyLabel}</div>;
  }

  const head = (speaker?: string, tsMs?: number) =>
    speaker ? (
      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--t2)", marginBottom: 3 }}>
        {speaker}
        {typeof tsMs === "number" && (
          <span style={{ fontWeight: 400, color: "var(--t3)", marginLeft: 8 }}>{new Date(tsMs).toLocaleTimeString()}</span>
        )}
      </div>
    ) : null;

  // dedupe tags by lowercased label (a keyword mentioned twice in a block shows once)
  const chips = (tags: EngineTag[]) => {
    const seen = new Set<string>();
    const uniq = tags.filter((t) => t.label && !seen.has(t.label.toLowerCase()) && seen.add(t.label.toLowerCase()));
    if (!uniq.length) return null;
    return (
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
        {uniq.map((t, i) => {
          const hue = TAG_HUE[t.kind] ?? "var(--t2)";
          return (
            <span key={`${t.label}-${i}`} title={`research ${t.label}`}
              style={{ fontSize: 11, color: hue, border: `1px solid ${hue}`, background: "transparent", borderRadius: 999, padding: "1px 8px", lineHeight: 1.5, opacity: 0.9 }}>
              {t.label}
            </span>
          );
        })}
      </div>
    );
  };

  // Signal badges attach to the LAST confirmed block (the running edge of the conversation). Only in
  // processed mode (signals supplied). Clickable → onSignal (create task / fact-check via useActions).
  const signalBadges = (forLastBlock: boolean) => {
    if (!forLastBlock || !signals || !signals.length) return null;
    return (
      <div role="group" aria-label="signals" style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
        {signals.map((sig) => {
          const hue = SIGNAL_HUE[sig.kind] ?? "var(--t2)";
          return (
            <button key={sig.id} type="button" title={`${sig.kind}: ${sig.label}`}
              onClick={() => actions?.onSignal?.(sig)}
              style={{
                display: "inline-flex", alignItems: "center", gap: 5, cursor: "pointer", fontSize: 11,
                color: hue, border: `1px solid ${hue}`, background: "transparent", borderRadius: 6,
                padding: "1px 7px", lineHeight: 1.5, fontWeight: 600,
              }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: hue, flex: "none" }} />
              {sig.kind}
            </button>
          );
        })}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 13, maxWidth: 760 }}>
      {blocks.map((b, idx) => {
        const isLast = idx === blocks.length - 1;
        return (
          <div key={b.key}>
            {head(b.speaker, b.tsMs)}
            <div style={{ fontSize: 13.5, color: "var(--t1)", lineHeight: 1.6 }}>
              <BlockText text={b.text} entities={entities} actions={actions} />
              {isLast && liveJoinsLast && (
                <span style={{ color: "var(--t3)", fontStyle: "italic" }}> {live} …</span>
              )}
            </div>
            {chips(b.tags)}
            {signalBadges(isLast && !liveOwnBlock)}
          </div>
        );
      })}
      {liveOwnBlock && (
        <div>
          {head(liveSpeaker)}
          <div style={{ fontSize: 13.5, color: "var(--t3)", lineHeight: 1.6, fontStyle: "italic" }}>{live} …</div>
          {signalBadges(true)}
        </div>
      )}
    </div>
  );
}

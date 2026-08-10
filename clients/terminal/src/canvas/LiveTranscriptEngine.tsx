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
import { entityColor } from "../ui-kit/docLinks";
import { usePlaybackSync } from "./playbackSync";

export interface EngineTag { label: string; kind: string }
export interface EngineEntity { id?: string; label: string; kind: string; docPath?: string }
export interface EngineSignal { id: string; kind: string; label: string }
export interface EngineSegment { speaker?: string; text: string; ts?: number | string; tsMs?: number; id?: string; completed?: boolean; tags?: EngineTag[] }

/** Relative seconds → m:ss, the recording-clock label a recorded line seeks to. */
function fmtClock(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export interface EngineActions {
  research?(entity: { id?: string; name: string; kind: string }): void;
  openEntityDoc?(entity: { id?: string; name: string; kind: string; docPath?: string }): void;
  onSignal?(signal: EngineSignal): void;
}

// Entity hues come from the ONE client-wide map (ui-kit/docLinks ENTITY_CHIP) so inline
// transcript highlights match [[wikilink]] chips exactly. Signals use semantic tokens.
const SIGNAL_HUE: Record<string, string> = { decision: "var(--violet)", "action-item": "var(--green)", action: "var(--green)", question: "var(--blue)", claim: "var(--warn)" };

function hueFor(kind: string): string {
  return entityColor(kind) ?? "var(--t2)";
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
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };  // consume: close-topmost beats nav.back
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

function ContestedText({ text }: { text: string }): React.ReactElement | null {
  const pattern = /⟦([^⟧]+)⟧\{([^}]+)\}/g;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) != null) {
    if (match.index > cursor) parts.push(text.slice(cursor, match.index));
    parts.push(
      <span
        key={`contest-${match.index}`}
        data-contested="true"
        aria-label="Unresolved live transcript"
        style={{
          display: "inline",
          color: "var(--t3)",
          fontStyle: "italic",
        }}
      >
        {match[1]}
      </span>,
    );
    cursor = pattern.lastIndex;
  }
  if (!parts.length) return null;
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}

/** Render a block's text. With `entities` → inline highlights; without → plain text (raw mode). */
function BlockText({ text, entities, actions }: { text: string; entities?: EngineEntity[]; actions?: EngineActions }) {
  if (/⟦[^⟧]+⟧\{[^}]+\}/.test(text)) return <ContestedText text={text} />;
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
  live: isLive,
  emptyLabel = "Waiting for transcript…",
  entities,
  signals,
  actions,
}: {
  segments: EngineSegment[];
  /** Live meeting → auto-scroll follows the growing transcript. */
  live?: boolean;
  emptyLabel?: string;
  entities?: EngineEntity[];
  signals?: EngineSignal[];
  actions?: EngineActions;
}) {
  // Confirmed (completed !== false) = stable. Merge consecutive same-speaker confirmed segments into
  // flowing blocks; keyword tags accumulate per block. Pending (completed === false) = the live edge.
  const blocks: { speaker?: string; ts?: number | string; tsMs?: number; text: string; key: string; tags: EngineTag[] }[] = [];
  for (const s of segments) {
    if (s.completed === false) continue;
    const last = blocks[blocks.length - 1];
    if (last && last.speaker === s.speaker) { last.text += " " + s.text; if (s.tags) last.tags.push(...s.tags); }
    else blocks.push({ speaker: s.speaker, ts: s.ts, tsMs: s.tsMs, text: s.text, key: s.id ?? `b${blocks.length}`, tags: [...(s.tags ?? [])] });
  }
  const lastPending = [...segments].reverse().find((s) => s.completed === false);
  const live = (lastPending?.text ?? "").trim();
  const liveSpeaker = lastPending?.speaker;

  const lastBlock = blocks[blocks.length - 1];
  const liveJoinsLast = !!live && !!lastBlock && lastBlock.speaker === liveSpeaker;
  const liveOwnBlock = !!live && !liveJoinsLast;

  const sync = usePlaybackSync();
  const clickable = !!sync?.hasPlayer;   // a recording is loaded → lines seek it on click

  // Each block's start in RELATIVE seconds (the media clock). A RECORDED line's `ts` is an ABSOLUTE epoch
  // (seconds); anchor it to the recording's TRUE start when the player knows it (recStartMs = end −
  // duration), else the earliest line. Live lines carry relative `ts` + absolute `tsMs`; they have no
  // player, so anchorSec ≈ their own earliest value and startSec is unused (live display uses tsMs).
  const tsSecOf = (b: { ts?: number | string }): number | undefined => {
    const t = typeof b.ts === "string" ? parseFloat(b.ts) : b.ts;
    return typeof t === "number" && Number.isFinite(t) ? t : undefined;
  };
  const minTsSec = blocks.reduce<number | null>((m, b) => { const t = tsSecOf(b); return t != null ? (m == null ? t : Math.min(m, t)) : m; }, null);
  const maxTsSec = blocks.reduce<number | null>((m, b) => { const t = tsSecOf(b); return t != null ? (m == null ? t : Math.max(m, t)) : m; }, null);
  const recStartSec = (sync?.recStartMs != null && Number.isFinite(sync.recStartMs)) ? sync.recStartMs / 1000 : null;
  // Trust the player's recording-start anchor only when it's a sane lower bound within the meeting span —
  // a PRE-FIX recording carries a bogus first_chunk_at (master-assembly time, well after the meeting), and
  // anchoring to that would collapse every line to 0. Otherwise fall back to the earliest line.
  const anchorSec = (recStartSec != null && (maxTsSec == null || recStartSec <= maxTsSec + 60)) ? recStartSec : minTsSec;
  const anchorMs = blocks.reduce<number | null>((m, b) => (typeof b.tsMs === "number" ? (m == null ? b.tsMs : Math.min(m, b.tsMs)) : m), null);
  const startSecOf = (b: { ts?: number | string; tsMs?: number }): number | undefined => {
    const t = tsSecOf(b);
    if (t != null && anchorSec != null) return Math.max(0, t - anchorSec);
    return typeof b.tsMs === "number" && anchorMs != null ? (b.tsMs - anchorMs) / 1000 : undefined;
  };
  // Read by the playback subscriber (which is set up once) — always the latest block times.
  const startSecsRef = useRef<(number | undefined)[]>([]);
  startSecsRef.current = blocks.map(startSecOf);

  // ── Live auto-scroll: stick to the bottom while pinned there; detach on scroll-up, re-attach at bottom.
  const [pinned, setPinned] = useState(true);
  const pinnedRef = useRef(true); pinnedRef.current = pinned;
  useEffect(() => {
    const el = sync?.scrollerRef.current;
    if (!isLive || !el) return;
    const onScroll = () => {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 120;
      if (nearBottom !== pinnedRef.current) setPinned(nearBottom);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [isLive, sync]);
  useEffect(() => {
    const el = sync?.scrollerRef.current;
    if (!isLive || !el || !pinnedRef.current) return;
    requestAnimationFrame(() => { const s = sync?.scrollerRef.current; if (s && pinnedRef.current) s.scrollTop = s.scrollHeight; });
  }, [isLive, sync, blocks.length, live]);

  // ── Playback follow (recorded): track the block under the play head; re-render only when it CHANGES.
  const [activeIdx, setActiveIdx] = useState(-1);
  const activeElRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!clickable || !sync) { setActiveIdx(-1); return; }
    let cur = -1;
    const findActive = (sec: number): number => {
      const secs = startSecsRef.current;
      let idx = -1;
      for (let i = 0; i < secs.length; i++) { const s = secs[i]; if (typeof s === "number") { if (s <= sec + 0.25) idx = i; else break; } }
      return idx;
    };
    return sync.subscribeTime((sec, playing) => {
      const idx = (playing || sec > 0.05) ? findActive(sec) : -1;
      if (idx !== cur) { cur = idx; setActiveIdx(idx); }
    });
  }, [clickable, sync]);
  // Ease the active line into view when it changes (not on every tick).
  useEffect(() => {
    if (activeIdx < 0) return;
    requestAnimationFrame(() => activeElRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }));
  }, [activeIdx]);

  if (!blocks.length && !live) {
    return <div style={{ color: "var(--t3)", fontSize: 13, padding: "8px 2px" }}>{emptyLabel}</div>;
  }

  const head = (speaker?: string, tsMs?: number, startSec?: number) =>
    speaker ? (
      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--t2)", marginBottom: 3 }}>
        {speaker}
        {typeof tsMs === "number" ? (
          <span style={{ fontWeight: 400, color: "var(--t3)", marginLeft: 8 }}>{new Date(tsMs).toLocaleTimeString()}</span>
        ) : typeof startSec === "number" ? (
          <span style={{ fontWeight: 400, color: clickable ? "var(--accent)" : "var(--t3)", marginLeft: 8, fontVariantNumeric: "tabular-nums" }}>{fmtClock(startSec)}</span>
        ) : null}
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
          const hue = hueFor(t.kind);
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
              onClick={(e) => { e.stopPropagation(); actions?.onSignal?.(sig); }}
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
        const startSec = startSecOf(b);
        const isActive = idx === activeIdx;
        const canSeek = clickable && typeof startSec === "number";
        return (
          <div
            key={b.key}
            ref={isActive ? activeElRef : undefined}
            onClick={canSeek ? () => sync!.seekTo(startSec!) : undefined}
            title={canSeek ? `Jump to ${fmtClock(startSec!)}` : undefined}
            style={{
              cursor: canSeek ? "pointer" : undefined,
              borderLeft: `2px solid ${isActive ? "var(--accent)" : "transparent"}`,
              background: isActive ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "transparent",
              borderRadius: 6, padding: "2px 8px", margin: "0 -8px", transition: "background 120ms",
            }}
          >
            {head(b.speaker, b.tsMs, startSec)}
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
        <div style={{ padding: "2px 8px", margin: "0 -8px" }}>
          {head(liveSpeaker)}
          <div style={{ fontSize: 13.5, color: "var(--t3)", lineHeight: 1.6, fontStyle: "italic" }}>{live} …</div>
          {signalBadges(true)}
        </div>
      )}
      {isLive && !pinned && (
        <button
          type="button"
          onClick={() => { const s = sync?.scrollerRef.current; if (s) s.scrollTop = s.scrollHeight; setPinned(true); }}
          style={{
            position: "sticky", bottom: 8, alignSelf: "center", cursor: "pointer",
            background: "var(--accent)", color: "var(--on-accent)", border: "none",
            borderRadius: 999, padding: "4px 13px", fontSize: 12, fontWeight: 600,
            boxShadow: "0 4px 14px rgba(0,0,0,0.22)",
          }}
        >
          ↓ Jump to latest
        </button>
      )}
    </div>
  );
}

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

import "./liveTranscript.css";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { splitTextIntoSpans, type SpanEntity } from "./inlineSpans";
import { useMeetingPlayback, seekMeeting, segmentSeconds } from "../minutes/meetingPlayback";
import { playbackTime } from "../minutes/RecordingPlayer";
import { entityColor } from "../ui-kit/docLinks";
import { splitEmphasis, type MarksFor, type SegmentMarks } from "./signalMarks";

export interface EngineTag { label: string; kind: string }
export interface EngineEntity { id?: string; label: string; kind: string; docPath?: string }
export interface EngineSignal { id: string; kind: string; label: string }
export interface EngineSegment { speaker?: string; text: string; ts?: number | string; tsMs?: number; endMs?: number; id?: string; completed?: boolean; tags?: EngineTag[] }

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
        className="vx-lt-mention"
        style={{ "--lt-hue": hue } as CSSProperties}
      >
        {entity.label}
      </span>
      {open && (
        <span
          role="menu"
          className="vx-lt-menu"
          style={{ position: "absolute", top: "100%", left: 0, minWidth: 150, display: "flex", flexDirection: "column" }}
        >
          <button type="button" role="menuitem" onClick={run(actions?.research)} className="vx-lt-menu-item">Research</button>
          <button type="button" role="menuitem" onClick={run(actions?.openEntityDoc)} className="vx-lt-menu-item">Open entity doc</button>
        </span>
      )}
    </span>
  );
}

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

/** Render a block's text. With `renderText` → whatever that layer draws; with `entities` → inline
 *  highlights; with neither → plain text (raw mode).
 *
 *  `renderText` is a SEAM, not a feature: the transcript-terms layer (PRD decision 35) draws chips
 *  over the same words, and it is a separate component so that this engine stays one renderer and
 *  the in-product inference pipeline can be removed from underneath it (decision 34) without the
 *  chips going with it. It wins over `entities` because a caller that supplies both has said which
 *  layer it wants on top, and two highlight layers over one string would fight for the same spans. */
function BlockText({ text, entities, actions, renderText }: { text: string; entities?: EngineEntity[]; actions?: EngineActions; renderText?: (text: string) => React.ReactNode }) {
  if (/⟦[^⟧]+⟧\{[^}]+\}/.test(text)) return <ContestedText text={text} />;
  if (renderText) return <>{renderText(text)}</>;
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

/** A line's text with its significant words in a heavier weight (transcript signals). The word
 *  pieces go through the same `renderText` layer as the rest, so term chips still draw. */
function EmphasizedText({ text, words, renderText }: { text: string; words: string[]; renderText?: (text: string) => React.ReactNode }) {
  return <>{splitEmphasis(text, words).map((p, i) => p.strong
    ? <strong key={i} data-signal-word="" className="vx-lt-sigword">{renderText ? renderText(p.text) : p.text}</strong>
    : <span key={i}>{renderText ? renderText(p.text) : p.text}</span>)}</>;
}

/** A line's wake-label chips: small, quiet, after the words they belong to. */
function SignalChips({ marks }: { marks: SegmentMarks }) {
  if (!marks.chips.length) return null;
  return <>{marks.chips.map((c) => (
    <span key={c.name} data-signal-chip={c.name} className="vx-lt-sigchip" aria-label={`Signal: ${c.label}`}
      title={`${c.label} · ${Math.round(c.score * 100)}%`}>{c.label}</span>
  ))}</>;
}

/** `text` with every case-insensitive occurrence of `query` marked — the transcript's search. */
export function Marked({ text, query }: { text: string; query: string }): React.ReactElement {
  const q = query.trim().toLowerCase();
  if (!q) return <>{text}</>;
  const out: React.ReactNode[] = [];
  const low = text.toLowerCase();
  let at = 0;
  for (let i = low.indexOf(q); i >= 0; i = low.indexOf(q, i + q.length)) {
    if (i > at) out.push(text.slice(at, i));
    out.push(<mark key={i} data-transcript-match className="vx-lt-mark">{text.slice(i, i + q.length)}</mark>);
    at = i + q.length;
  }
  if (at < text.length) out.push(text.slice(at));
  return <>{out}</>;
}

/** Does a speaker turn match the search? Its words or its speaker's name. */
export function turnMatches(turn: { speaker?: string; text: string }, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || turn.text.toLowerCase().includes(q) || (turn.speaker ?? "").toLowerCase().includes(q);
}

export function LiveTranscriptEngine({
  segments,
  meetingId,
  emptyLabel = "Nothing said yet\u2026",
  entities,
  signals,
  actions,
  renderText,
  layout = "stack",
  query = "",
  gaps = [],
  marks,
}: {
  segments: EngineSegment[];
  meetingId?: string;
  emptyLabel?: string;
  entities?: EngineEntity[];
  signals?: EngineSignal[];
  actions?: EngineActions;
  /** an outer layer's per-block renderer (decision 35's term chips). See `BlockText`. */
  renderText?: (text: string) => React.ReactNode;
  /** `rows` — the meeting view's conversational layout: who and when in a fixed left column
   *  (tabular figures), what was said beside it, smaller than page prose. Default `stack` is the
   *  original speaker-over-text layout every other caller keeps. */
  layout?: "stack" | "rows";
  /** the transcript search: only the speaker turns that match are drawn, the match marked */
  query?: string;
  /** Captured audio never transcribed (prod-cutover R4): a quiet marker at its place in time. */
  gaps?: { startMs?: number; endMs?: number; label: string }[];
  /** TRANSCRIPT SIGNALS (rows layout): per line, its wake-label chips and emphasised words. Absent,
   *  or nothing for a line, and the line renders exactly as without it. */
  marks?: MarksFor;
}) {
  const playback = useMeetingPlayback(meetingId);
  // "Play from m:ss" is drawn here, not as a native `title`: a native tooltip floats wherever the
  // browser puts it (it landed on the page header's buttons). This one sits just above the line of
  // text under the pointer, horizontally at the pointer.
  const [tip, setTip] = useState<{ text: string; x: number; y: number } | null>(null);
  const placeTip = (e: React.MouseEvent<HTMLElement>, text: string) => {
    const rects = Array.from(e.currentTarget.getClientRects());
    const line = rects.find(r => e.clientY >= r.top && e.clientY <= r.bottom) ?? rects[0];
    setTip({ text, x: e.clientX, y: line ? line.top : e.clientY });
  };
  const timed = segments.filter(s => s.completed !== false).map(s => ({ segment: s, timing: segmentSeconds(s, playback.originMs) }));
  // Explicit ends keep silence unhighlighted; start-only transcripts advance at the next passage.
  const active = playback.time === null ? undefined : [...timed].reverse().find(item => {
    if (!item.timing || playback.time! < item.timing.start) return false;
    const end = item.timing.end ?? timed.find(x => x.timing && x.timing.start > item.timing!.start)?.timing?.start;
    return end !== undefined && playback.time! < end;
  })?.segment;
  const passage = (s: EngineSegment, index: number) => {
    const timing = segmentSeconds(s, playback.originMs);
    const canSeek = !!meetingId && playback.available && !!timing;
    const current = active === s;
    const m = marks?.(s.id);
    const words = query.trim() ? <Marked text={s.text} query={query} />
      : m?.words.length && !/⟦[^⟧]+⟧\{[^}]+\}/.test(s.text) ? <EmphasizedText text={s.text} words={m.words} renderText={renderText} />
      : <BlockText text={s.text} entities={entities} actions={actions} renderText={renderText} />;
    const line = <span key={s.id ?? index} data-playback-active={current || undefined}
      role={canSeek ? "button" : undefined} tabIndex={canSeek ? 0 : undefined}
      aria-label={canSeek ? `Play from ${playbackTime(timing!.start)}: ${s.text}` : undefined}
      aria-current={current ? "true" : undefined}
      onMouseMove={canSeek ? e => placeTip(e, `Play from ${playbackTime(timing!.start)}`) : undefined}
      onMouseLeave={canSeek ? () => setTip(null) : undefined}
      style={{ cursor: canSeek ? "pointer" : undefined, borderRadius: 3, background: current ? "color-mix(in srgb, var(--blue) 20%, transparent)" : undefined }}
      onClick={e => {
        if (!canSeek || window.getSelection()?.toString()) return;
        const interactive = (e.target as Element).closest("a,button,input,[role=button],[role=menu]");
        if (interactive && interactive !== e.currentTarget) return;
        seekMeeting(meetingId!, timing!.start);
      }}
      onKeyDown={e => {
        if (canSeek && e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault(); seekMeeting(meetingId!, timing!.start);
        }
      }}>{words}</span>;
    return m ? <span key={s.id ?? index} data-signal-segment={s.id}>{line}<SignalChips marks={m} /></span> : line;
  };
  // Confirmed (completed !== false) = stable. Merge consecutive same-speaker confirmed segments into
  // flowing blocks; keyword tags accumulate per block. Pending (completed === false) = the live edge.
  const blocks: { speaker?: string; tsMs?: number; text: string; key: string; tags: EngineTag[]; segments: EngineSegment[]; gap?: string }[] = [];
  // A gap sits between the words around it, by time; it breaks a same-speaker run (the words on
  // either side were not one continuous passage). A gap with no time goes last.
  const pendingGaps = [...gaps].filter((g) => g.label).sort((a, b) => (a.startMs ?? Infinity) - (b.startMs ?? Infinity));
  const flushGaps = (beforeMs?: number) => {
    while (pendingGaps.length && (beforeMs === undefined || (pendingGaps[0].startMs ?? Infinity) <= beforeMs)) {
      const g = pendingGaps.shift()!;
      blocks.push({ tsMs: g.startMs, text: "", key: `gap-${g.startMs ?? blocks.length}-${blocks.length}`, tags: [], segments: [], gap: g.label });
    }
  };
  for (const s of segments) {
    if (s.completed === false) continue;
    if (typeof s.tsMs === "number") flushGaps(s.tsMs);
    const last = blocks[blocks.length - 1];
    if (last && !last.gap && last.speaker === s.speaker) { last.text += " " + s.text; last.segments.push(s); if (s.tags) last.tags.push(...s.tags); }
    else blocks.push({ speaker: s.speaker, tsMs: s.tsMs, text: s.text, key: s.id ?? `b${blocks.length}`, tags: [...(s.tags ?? [])], segments: [s] });
  }
  flushGaps();
  const gapMarker = (key: string, label: string) => (
    <div key={key} data-transcript-gap role="note" className="vx-lt-gap">{label}</div>
  );
  const lastPending = [...segments].reverse().find((s) => s.completed === false);
  const liveSpeaker = lastPending?.speaker;
  // Every draft of that speaker still awaiting reconfirmation, in order (show fast, then reconfirm:
  // the drafts stay visible, dimmed, until the reconfirmed segment replaces them).
  const live = segments
    .filter((s) => s.completed === false && s.speaker === liveSpeaker && (s.text ?? "").trim())
    .map((s) => s.text.trim()).join(" ");

  const lastBlock = blocks[blocks.length - 1];
  const liveJoinsLast = !!live && !!lastBlock && !lastBlock.gap && lastBlock.speaker === liveSpeaker;
  const liveOwnBlock = !!live && !liveJoinsLast;

  if (!blocks.length && !live) {
    return <div className="vx-lt-empty">{emptyLabel}</div>;
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

  if (layout === "rows" && query.trim()) {
    const hits = blocks.filter((b) => !b.gap && turnMatches(b, query));
    if (!hits.length) return <div className="vx-lt-empty" data-transcript-no-match>Nothing matches “{query.trim()}”.</div>;
    blocks.splice(0, blocks.length, ...hits);
  }
  if (layout === "rows") {
    const clock = (tsMs?: number) => typeof tsMs === "number"
      ? new Date(tsMs).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    // SPEAKER AND TIME ON ONE LINE ABOVE THE WORDS (founder, UI lab 2026-10-14: "too much space
    // lost"): no left column, so a long turn wraps the full width of the drawer.
    const row = (key: string, speaker: string | undefined, tsMs: number | undefined, body: React.ReactNode, extra?: React.ReactNode) => (
      <div key={key} data-transcript-row className="vx-lt-row">
        <div data-transcript-who className="vx-lt-who">
          <span className="vx-lt-speaker" title={speaker}>{speaker ?? ""}</span>
          <span className="vx-lt-clock">{clock(tsMs)}</span>
        </div>
        <div className="vx-lt-body">{body}{extra}</div>
      </div>
    );
    return (
      <div className="vx-lt-rows">
        {blocks.map((b, idx) => {
          if (b.gap) return gapMarker(b.key, b.gap);
          const isLast = idx === blocks.length - 1;
          return row(b.key, b.speaker, b.tsMs,
            <div className="vx-lt-words">
              {playback.available || marks ? b.segments.map((s, i) => <span key={s.id ?? i}>{i > 0 ? " " : ""}{passage(s, i)}</span>)
                : query.trim() ? <Marked text={b.text} query={query} /> : <BlockText text={b.text} entities={entities} actions={actions} renderText={renderText} />}
              {isLast && liveJoinsLast && !query.trim() && <span style={{ color: "var(--t3)", fontStyle: "italic" }}> {live} …</span>}
            </div>,
            <>{chips(b.tags)}{signalBadges(isLast && !liveOwnBlock)}</>);
        })}
        {liveOwnBlock && !query.trim() && row("live", liveSpeaker, lastPending?.tsMs,
          <div style={{ fontSize: 13, color: "var(--t3)", lineHeight: 1.55, fontStyle: "italic" }}>{live} …</div>)}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 13, maxWidth: 760 }}>
      {blocks.map((b, idx) => {
        if (b.gap) return gapMarker(b.key, b.gap);
        const isLast = idx === blocks.length - 1;
        return (
          <div key={b.key}>
            {head(b.speaker, b.tsMs)}
            <div style={{ fontSize: 13.5, color: "var(--t1)", lineHeight: 1.6 }}>
              {playback.available ? b.segments.map((s, i) => <span key={s.id ?? i}>{i > 0 ? " " : ""}{passage(s, i)}</span>) : <BlockText text={b.text} entities={entities} actions={actions} renderText={renderText} />}
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
      {tip && typeof document !== "undefined" && createPortal(
        <div role="tooltip" data-transcript-tip style={{ position: "fixed", left: tip.x, top: tip.y - 4, transform: "translate(-50%, -100%)",
          zIndex: 70, pointerEvents: "none", whiteSpace: "nowrap", fontSize: 12, padding: "3px 8px", borderRadius: 6,
          background: "var(--panel2)", color: "var(--t1)", border: "1px solid var(--line2)", boxShadow: "0 4px 14px rgba(0,0,0,.25)" }}>
          {tip.text}
        </div>, document.body)}
    </div>
  );
}

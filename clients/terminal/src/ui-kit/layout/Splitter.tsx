"use client";
/** Splitter (guidelines §3.2) — the resize handle between a side pane and the conversation.
 *
 *  A real `role="separator"`: focusable, `aria-valuenow/min/max`, and keyboard-operable —
 *  ←/→ move 16px, Shift+←/→ 64px, Home/End jump to the bounds, Enter (or a double-click) resets to
 *  the default. The hit target is 8px wide; the visible line is 1px and turns accent on hover,
 *  focus and drag (CSS, `.vx-splitter`).
 *
 *  `grows` says which pointer direction makes the pane wider: a pane on the LEFT grows when the
 *  splitter moves right, a pane on the RIGHT when it moves left. Arrow keys follow the pointer, so
 *  ArrowRight always moves the line right, whichever pane that widens.
 *
 *  Dragging uses pointer capture on the handle itself, so no window listeners outlive a drag, and
 *  `user-select: none` is set on the document only while one is in progress. */
import { useRef, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import type { Bounds } from "./shellLayout";

export const SPLITTER_STEP = 16;
export const SPLITTER_BIG_STEP = 64;

export type SplitterProps = {
  label: string;
  value: number;
  bounds: Bounds;
  /** "right": the pane is left of the splitter and grows as it moves right; "left": the opposite. */
  grows: "right" | "left";
  /** Render a width while it is being dragged. */
  onPreview: (w: number) => void;
  /** Remember a width; `null` = reset to the default. */
  onCommit: (w: number | null) => void;
  /** Layout geometry only: where the handle sits. */
  style?: CSSProperties;
};

const clamp = (n: number, b: Bounds) => Math.min(b.max, Math.max(b.min, Math.round(n)));

/** The key → new width rule, exported so it is testable without a DOM. `null` = reset. */
export function splitterKey(key: string, shift: boolean, value: number, bounds: Bounds, grows: "right" | "left"): number | null | undefined {
  const step = shift ? SPLITTER_BIG_STEP : SPLITTER_STEP;
  const sign = grows === "right" ? 1 : -1;
  if (key === "ArrowRight") return clamp(value + sign * step, bounds);
  if (key === "ArrowLeft") return clamp(value - sign * step, bounds);
  if (key === "Home") return bounds.min;
  if (key === "End") return bounds.max;
  if (key === "Enter") return null;
  return undefined;   // not ours
}

export function Splitter({ label, value, bounds, grows, onPreview, onCommit, style }: SplitterProps) {
  const drag = useRef<{ x: number; w: number; last: number } | null>(null);

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    drag.current = { x: e.clientX, w: value, last: value };
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* jsdom / old engines */ }
    document.documentElement.dataset.resizing = "";
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const next = clamp(d.w + (grows === "right" ? dx : -dx), bounds);
    if (next !== d.last) { d.last = next; onPreview(next); }
  };
  const end = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    delete document.documentElement.dataset.resizing;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* not captured */ }
    onCommit(d.last);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const next = splitterKey(e.key, e.shiftKey, value, bounds, grows);
    if (next === undefined) return;
    e.preventDefault();
    onCommit(next);
  };

  return (
    <div role="separator" aria-orientation="vertical" aria-label={label} tabIndex={0}
      aria-valuenow={value} aria-valuemin={bounds.min} aria-valuemax={bounds.max}
      className="vx-splitter" data-splitter={grows === "right" ? "start" : "end"}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={end} onPointerCancel={end}
      onDoubleClick={() => onCommit(null)} onKeyDown={onKeyDown} style={style}>
      <span className="vx-splitter-line" aria-hidden />
    </div>
  );
}

"use client";
/** THE ENTITY HOVER CARD (founder, 2026-10-15, on a page table: "this takes too much space — make it
 *  small and only on hover, click should open page"; then "so this should be some things on hover").
 *
 *  One small card for every entity, person and company chip — in pages, chat replies, tables and
 *  the home: an avatar or the type's icon, the name, and at most three short lines (`hoverFacts`).
 *  No property table, no Open button, no ×. It appears after a short delay on hover or keyboard
 *  focus (a long press on touch), goes on mouse-out, blur or Esc, and sits BELOW the chip's line
 *  (above it when there is no room), so it never covers the row the chip is in. A click on the chip
 *  opens the page; the card itself takes no clicks. */
import { useEffect, useId, useRef, useState, type FocusEvent, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

export const HOVER_DELAY_MS = 350;
export const LONG_PRESS_MS = 500;
const WIDTH = 260;
const GAP = 6;

export type HoverSubject = {
  name: string;
  /** the avatar or the type's icon, drawn as given */
  mark?: ReactNode;
  /** the lines, or a loader for them (read once, when the card first opens) */
  lines?: string[];
  load?: () => Promise<string[]>;
  /** a stable key for the loader's cache */
  cacheKey?: string;
};

const cache = new Map<string, Promise<string[]>>();
/** A story that arrived after the card was read (the server's one-time backfill): the next hover shows it. */
export function primeHoverCache(key: string, lines: string[]) { cache.set(key, Promise.resolve(lines)); }
/** Tests: forget what was read. */
export function resetHoverCache() { cache.clear(); }

type Bind = {
  "aria-describedby"?: string;
  onMouseEnter: (e: MouseEvent<HTMLElement>) => void;
  onMouseLeave: () => void;
  onFocus: (e: FocusEvent<HTMLElement>) => void;
  onBlur: () => void;
  onPointerDown: (e: PointerEvent<HTMLElement>) => void;
  onPointerUp: () => void;
  onPointerCancel: () => void;
  onClickCapture: (e: MouseEvent<HTMLElement>) => void;
  onKeyDownCapture: (e: { key: string }) => void;
};

/** `{ bind, card }`: spread `bind` on the chip, render `card` anywhere (it portals to the body). */
export function useHoverCard(subject: HoverSubject | null): { bind: Bind | Record<string, never>; card: ReactNode } {
  const id = useId();
  const [at, setAt] = useState<DOMRect | null>(null);
  const [lines, setLines] = useState<string[] | null>(subject?.lines ?? null);
  const timer = useRef<number | undefined>(undefined);
  const pressed = useRef(false);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const staticLines = subject?.lines ? subject.lines.join("\n") : null;
  useEffect(() => { setLines(staticLines === null ? null : staticLines.split("\n").filter(Boolean)); }, [subject?.cacheKey, staticLines]);
  const loader = useRef(subject?.load);
  loader.current = subject?.load;
  const key = subject ? subject.cacheKey ?? subject.name : "";
  useEffect(() => {
    if (!at || lines || !loader.current) return;
    let live = true;
    if (!cache.has(key)) cache.set(key, loader.current().catch(() => []));
    void cache.get(key)!.then((l) => { if (live) setLines(l); });
    return () => { live = false; };
  }, [at, lines, key]);
  if (!subject) return { bind: {}, card: null };
  const show = (el: HTMLElement, delay: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setAt(el.getBoundingClientRect()), delay);
  };
  // read again from the cache on the next show: a story backfilled meanwhile is there now
  const hide = () => { window.clearTimeout(timer.current); setAt(null); if (!subject.lines) setLines(null); };
  const bind: Bind = {
    "aria-describedby": at ? id : undefined,
    onMouseEnter: (e) => show(e.currentTarget, HOVER_DELAY_MS),
    onMouseLeave: hide,
    onFocus: (e) => show(e.currentTarget, HOVER_DELAY_MS),
    onBlur: hide,
    onPointerDown: (e) => {
      if (e.pointerType !== "touch") return;
      const el = e.currentTarget;
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => { pressed.current = true; setAt(el.getBoundingClientRect()); }, LONG_PRESS_MS);
    },
    onPointerUp: () => { if (!pressed.current) window.clearTimeout(timer.current); },
    onPointerCancel: hide,
    // a long press showed the card: the release that ends it is not a click that opens the page
    onClickCapture: (e) => { if (pressed.current) { pressed.current = false; e.preventDefault(); e.stopPropagation(); } else hide(); },
    onKeyDownCapture: (e) => { if (e.key === "Escape") hide(); },
  };
  const card = at && typeof document !== "undefined" ? createPortal(<HoverCardView id={id} at={at} subject={subject} lines={lines} />, document.body) : null;
  return { bind, card };
}

function HoverCardView({ id, at, subject, lines }: { id: string; at: DOMRect; subject: HoverSubject; lines: string[] | null }) {
  const vw = typeof window !== "undefined" ? window.innerWidth : 1024;
  const vh = typeof window !== "undefined" ? window.innerHeight : 768;
  const left = Math.max(8, Math.min(at.left, vw - WIDTH - 8));
  // below the chip's line; above it when the line is near the bottom of the window
  const below = vh - at.bottom > 140;
  const pos = below ? { top: at.bottom + GAP, left } : { bottom: vh - at.top + GAP, left };
  return (
    <div id={id} role="tooltip" className="vx-hovercard" data-hover-card="" style={pos}>
      <div className="vx-hovercard-head">
        {subject.mark && <span className="vx-hovercard-mark" aria-hidden>{subject.mark}</span>}
        <span className="vx-hovercard-name">{subject.name}</span>
      </div>
      {(lines ?? []).map((l, i) => <div key={i} className="vx-hovercard-line" data-hover-line={i}>{l}</div>)}
    </div>
  );
}

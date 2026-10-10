"use client";
/** OverflowStrip (guidelines §3.3, §4.6) — a row of tabs that never hides a tab silently.
 *
 *  Tabs keep a legible width and the row scrolls sideways, as a browser's does. What it adds over a
 *  bare scroller: a FADE at whichever edge has tabs beyond it, an OVERFLOW BUTTON ("3 more") that
 *  opens a menu of every tab whenever any tab is not fully visible, and the active tab scrolled into
 *  view whenever it changes. Before this the pages strip scrolled with its scrollbar hidden and no
 *  cue at all — "_global" was clipped to "_glob" and the rest were simply not there.
 *
 *  Each child that is a tab carries `data-strip-key={key}` matching an entry in `items`. */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Menu } from "./Menu";

export type StripItem = { key: string; label: string; onSelect: () => void };

export function OverflowStrip({ label, items, activeKey, children }: {
  label: string; items: StripItem[]; activeKey?: string; children: ReactNode;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const [state, setState] = useState({ hidden: 0, start: false, end: false });

  const measure = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const box = el.getBoundingClientRect();
    let hidden = 0;
    el.querySelectorAll<HTMLElement>("[data-strip-key]").forEach((t) => {
      const r = t.getBoundingClientRect();
      if (r.width === 0) return;
      if (r.left < box.left - 1 || r.right > box.right + 1) hidden += 1;
    });
    const start = el.scrollLeft > 1;
    const end = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    setState((s) => (s.hidden === hidden && s.start === start && s.end === end ? s : { hidden, start, end }));
  }, []);

  useLayoutEffect(() => { measure(); });
  useEffect(() => {
    const el = scroller.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure]);
  useEffect(() => {
    if (!activeKey) return;
    const el = scroller.current?.querySelector<HTMLElement>(`[data-strip-key="${activeKey.replace(/["\\]/g, "\\$&")}"]`);
    el?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [activeKey]);

  return (
    <div className="vx-strip" data-fade-start={state.start ? "" : undefined} data-fade-end={state.end ? "" : undefined}>
      <div ref={scroller} className="vx-strip-scroll" onScroll={measure}>{children}</div>
      {state.hidden > 0 && (
        <Menu label={`${state.hidden} more ${label}`} align="end" variant="ghost" triggerData={{ "data-strip-more": "" }}
          trigger={<><ChevronDown size={14} strokeWidth={1.75} aria-hidden /><span className="vx-strip-more-n">{state.hidden} more</span></>}
          items={items.map((it) => ({ key: it.key, label: it.label, checked: it.key === activeKey, onSelect: it.onSelect }))} />
      )}
    </div>
  );
}

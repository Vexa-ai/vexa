"use client";
/** Tabs (guidelines §4.6) — the WAI-ARIA tabs pattern: a `tablist` of `tab`s with a roving
 *  tabindex (only the selected tab is in the tab order), ←/→ move and select, Home/End jump.
 *  `underline` skin for sections and settings; `document` skin for page strips. Long strips go
 *  inside an OverflowStrip. */
import { useRef, type KeyboardEvent, type ReactNode } from "react";

export type TabItem = { key: string; label: ReactNode };

export function Tabs({ label, items, value, onChange, skin = "underline" }: {
  label: string; items: TabItem[]; value: string; onChange: (k: string) => void; skin?: "underline" | "document";
}) {
  const list = useRef<HTMLDivElement>(null);
  const onKey = (e: KeyboardEvent) => {
    const i = items.findIndex((t) => t.key === value);
    const to = e.key === "ArrowRight" ? (i + 1) % items.length : e.key === "ArrowLeft" ? (i - 1 + items.length) % items.length
      : e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : -1;
    if (to < 0) return;
    e.preventDefault();
    onChange(items[to].key);
    list.current?.querySelectorAll<HTMLElement>("[role=tab]")[to]?.focus();
  };
  return (
    <div ref={list} role="tablist" aria-label={label} className="vx-tabs" data-skin={skin} onKeyDown={onKey}>
      {items.map((t) => (
        <button key={t.key} type="button" role="tab" className="vx-tab" aria-selected={t.key === value}
          tabIndex={t.key === value ? 0 : -1} onClick={() => onChange(t.key)}>{t.label}</button>
      ))}
    </div>
  );
}

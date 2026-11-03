"use client";
/** Breadcrumb (guidelines §4.7) — sans `--text-sm`, ancestors in `--text-3`, the current item in
 *  `--text-1`, lucide chevrons. Past four segments the MIDDLE collapses into a "…" menu: the first,
 *  then "…", then the last two. Labels are names, never slugs (the caller passes registry names). */
import { ChevronRight } from "lucide-react";
import { Menu } from "./Menu";

export type Crumb = { key: string; label: string; onSelect?: () => void };

export function Breadcrumb({ items, label = "Breadcrumb" }: { items: Crumb[]; label?: string }) {
  const collapse = items.length > 4;
  const head = collapse ? items.slice(0, 1) : items.slice(0, -1);
  const middle = collapse ? items.slice(1, -2) : [];
  const tail = collapse ? items.slice(-2, -1) : [];
  const last = items[items.length - 1];
  const sep = <ChevronRight size={12} strokeWidth={1.75} className="vx-crumb-sep" aria-hidden />;
  const crumb = (c: Crumb) => (c.onSelect
    ? <button type="button" className="vx-crumb" onClick={c.onSelect}>{c.label}</button>
    : <span className="vx-crumb">{c.label}</span>);
  return (
    <nav aria-label={label} className="vx-breadcrumb">
      {head.map((c) => <span key={c.key} className="vx-crumb-item">{crumb(c)}{sep}</span>)}
      {middle.length > 0 && <span className="vx-crumb-item">
        <Menu label={`${middle.length} more folders`} trigger="…" items={middle.map((c) => ({ key: c.key, label: c.label, onSelect: () => c.onSelect?.() }))} />{sep}
      </span>}
      {tail.map((c) => <span key={c.key} className="vx-crumb-item">{crumb(c)}{sep}</span>)}
      {/* The last segment is the current page — unless it can be walked to (a document's folder
          trail ends at the folder, which is still a place to go). */}
      {last && (last.onSelect
        ? <span className="vx-crumb-item">{crumb(last)}</span>
        : <span className="vx-crumb vx-crumb-current" aria-current="page">{last.label}</span>)}
    </nav>
  );
}

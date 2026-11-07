"use client";
/** A PLAIN TABLE IN A PAGE IS INTERACTIVE (#1783, R3 `home2`; BUILD 2026-10-16 "plain tables become
 *  interactive (filter, sort)"). Search across every cell, filter a column by its value (columns with
 *  a handful of distinct values), sort by a header (again to reverse, a third time to restore). It
 *  only changes how the rows are SHOWN: the page's markdown is never written from here — editing is
 *  the ```view block's job, through the owner's write path. A table with no header renders as before. */
import { Children, isValidElement, useMemo, useState, type ReactElement, type ReactNode } from "react";
import "./home2.css";

const textOf = (n: ReactNode): string => {
  if (n == null || typeof n === "boolean") return "";
  if (typeof n === "string" || typeof n === "number") return String(n);
  if (Array.isArray(n)) return n.map(textOf).join("");
  if (isValidElement(n)) return textOf((n as ReactElement<{ children?: ReactNode }>).props.children);
  return "";
};

type Row = { cells: ReactNode[]; text: string[] };
/** rows (header first) from the rendered thead/tbody, keeping each cell's rendered content */
export function tableRows(children: ReactNode): Row[] {
  const out: Row[] = [];
  const walk = (n: ReactNode) => Children.forEach(n, (c) => {
    if (!isValidElement(c)) return;
    const el = c as ReactElement<{ children?: ReactNode }>;
    if (el.type === "tr") {
      const kids = Children.toArray(el.props.children).filter(isValidElement) as ReactElement<{ children?: ReactNode }>[];
      out.push({ cells: kids.map((k) => k.props.children), text: kids.map((k) => textOf(k.props.children).trim()) });
      return;
    }
    walk(el.props.children);
  });
  walk(children);
  return out;
}

const FILTERABLE_MAX = 8;

export function LiveTable({ children }: { children?: ReactNode }) {
  const parsed = useMemo(() => tableRows(children), [children]);
  const head = parsed[0];
  const rows = parsed.slice(1);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const [filters, setFilters] = useState<Record<number, string>>({});
  const filterable = useMemo(() => (head?.text ?? []).map((_, c) => {
    const vals = [...new Set(rows.map((r) => r.text[c] ?? ""))].filter(Boolean);
    return vals.length > 1 && vals.length <= FILTERABLE_MAX && vals.length < rows.length ? vals.sort() : null;
  }), [head, rows]);
  if (!head || rows.length < 2) return <div className="vx-prose-table" data-allow-hscroll=""><table>{children}</table></div>;
  const needle = q.trim().toLowerCase();
  const shown = rows
    .filter((r) => !needle || r.text.some((x) => x.toLowerCase().includes(needle)))
    .filter((r) => Object.entries(filters).every(([c, v]) => !v || r.text[Number(c)] === v))
    .map((r, i) => ({ r, i }))
    .sort((a, b) => (sort ? (a.r.text[sort.col] ?? "").localeCompare(b.r.text[sort.col] ?? "", undefined, { numeric: true }) * sort.dir : a.i - b.i));
  return (
    <div className="vx-lt" data-live-table="">
      <div className="vx-lt-bar">
        <input className="vx-lt-q" type="search" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search this table" />
        {filterable.map((vals, c) => vals && (
          <select key={c} className="vx-lt-q" aria-label={`Filter by ${head.text[c]}`} value={filters[c] ?? ""}
            onChange={(e) => setFilters((f) => ({ ...f, [c]: e.target.value }))}>
            <option value="">{head.text[c]}: all</option>
            {vals.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>))}
        <span className="vx-lt-count" aria-live="polite">{shown.length === rows.length ? `${rows.length} rows` : `${shown.length} of ${rows.length}`}</span>
      </div>
      <div className="vx-prose-table" data-allow-hscroll=""><table>
        <thead><tr>{head.cells.map((h, c) => (
          <th key={c} aria-sort={sort?.col === c ? (sort.dir === 1 ? "ascending" : "descending") : undefined}>
            <button type="button" className="vx-lt-sort" onClick={() => setSort((s) => (s?.col === c ? (s.dir === 1 ? { col: c, dir: -1 } : null) : { col: c, dir: 1 }))}>
              {h}{sort?.col === c ? (sort.dir === 1 ? " ↑" : " ↓") : ""}</button></th>))}</tr></thead>
        <tbody>{shown.map(({ r, i }) => <tr key={i}>{r.cells.map((cell, c) => <td key={c}>{cell}</td>)}</tr>)}</tbody>
      </table></div>
    </div>
  );
}

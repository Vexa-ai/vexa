"use client";
/** KeyValue (guidelines §4.16, §3.3) — a metadata table that never breaks a word.
 *
 *  `<dl>` semantics. The layout follows the width of ITS OWN PANE (a container query on the
 *  wrapper), not the viewport: at 360px and wider, two columns — the label `minmax(88px, 30%)`
 *  capped at 160, the value `minmax(0, 1fr)`; below 360, stacked, label above value. The same
 *  table therefore stacks correctly in a 320px pages panel on a 2560px monitor.
 *
 *  Values are rendered by type (`kvValue`): dates through `DateText`, URLs and emails through
 *  `Truncate` as links, arrays as a comma list, booleans as Yes/No, objects as a nested KeyValue —
 *  never `JSON.stringify`. Empty values are omitted. Over 8 rows, the first 6 show plus "Show all".
 *
 *  This replaces `overflowWrap: "anywhere"` on the old property grid, which split a domain as
 *  "car eers" and a date as "2026-10- 09" once the value column fell to ~80px. Text here wraps at
 *  spaces only (`overflow-wrap: break-word`); a token with no spaces truncates. */
import { useState, type ReactNode } from "react";
import { DateText } from "./DateText";
import { Truncate, displayUrl } from "./Truncate";
import { looksLikeDate } from "../format/date";

export type KeyValueItem = { key: string; label?: ReactNode; value: ReactNode };

const MAX_ROWS = 8;
const SHOW_ROWS = 6;

/** `snake_case` / `kebab-case` / `camelCase` → "Snake case". */
export function humanizeKey(key: string): string {
  const words = key.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").trim().toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : key;
}

const EMAIL = /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/;
const URL_RE = /^https?:\/\/\S+$/i;

/** Is this value empty enough to omit? */
export const isEmptyValue = (v: unknown): boolean =>
  v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0)
  || (typeof v === "object" && !Array.isArray(v) && !(v instanceof Date) && Object.keys(v as object).length === 0);

/** Render one value by its type. */
export function kvValue(value: unknown): ReactNode {
  if (value instanceof Date) return <DateText value={value} />;
  if (Array.isArray(value)) {
    const parts = value.filter((v) => !isEmptyValue(v));
    return <span className="vx-kv-list">{parts.map((v, i) => <span key={i} className="vx-kv-list-item">{kvValue(v)}</span>)}</span>;
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value && typeof value === "object") {
    const items = Object.entries(value as Record<string, unknown>).filter(([, v]) => !isEmptyValue(v))
      .map(([k, v]) => ({ key: k, value: kvValue(v) }));
    return <KeyValue items={items} nested />;
  }
  const text = String(value ?? "");
  if (URL_RE.test(text)) return <Truncate text={text} href={text}>{displayUrl(text)}</Truncate>;
  if (EMAIL.test(text)) return <Truncate text={text} href={`mailto:${text}`} />;
  if (looksLikeDate(text)) return <DateText value={text} />;
  // A single long token (an ID, a path) truncates in the middle; prose wraps at spaces.
  if (!/\s/.test(text) && text.length > 24) return <Truncate text={text} mode="middle" />;
  return text;
}

export function KeyValue({ items, nested, label, data }: {
  items: KeyValueItem[]; nested?: boolean; label?: string;
  data?: Record<string, string>;
}) {
  const [all, setAll] = useState(false);
  const rows = items.length > MAX_ROWS && !all ? items.slice(0, SHOW_ROWS) : items;
  return (
    <div className="vx-kv" data-nested={nested ? "" : undefined} aria-label={label} {...data}>
      <dl className="vx-kv-dl">
        {rows.map((it) => (
          <div key={it.key} className="vx-kv-row">
            <dt className="vx-kv-key">{it.label ?? humanizeKey(it.key)}</dt>
            <dd className="vx-kv-val">{it.value}</dd>
          </div>
        ))}
      </dl>
      {items.length > MAX_ROWS && (
        <button type="button" className="vx-kv-more" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${items.length}`}
        </button>
      )}
    </div>
  );
}

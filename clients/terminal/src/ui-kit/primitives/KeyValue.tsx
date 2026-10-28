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
const LABEL_KEYS = ["label", "title", "name", "source", "type", "kind"];
const DATE_KEYS = ["date", "at", "seen", "updated", "when", "on"];
const URL_KEYS = ["url", "href", "link"];
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v) && !(v instanceof Date);
const asDate = (v: unknown) => (v instanceof Date || (typeof v === "string" && looksLikeDate(v)) ? v as Date | string : null);

/** One item of a list of records — a page's `sources`, say — as ONE LINE: "Label · date", the label
 *  truncating (full text on hover), the date a nowrap unit, a link where the item has a URL.
 *  `{ Gmail: 2026-09-17 }` (a one-key record, the way a YAML flow list writes `[Gmail: 2026-09-17]`)
 *  reads the same as `{ source: Gmail, date: 2026-09-17 }`. Anything else is a nested table — on
 *  its own BLOCK line, never inside an inline list item. */
export function kvRecordLine(item: Record<string, unknown>): ReactNode {
  const entries = Object.entries(item).filter(([, v]) => !isEmptyValue(v));
  let label: string | undefined, date: Date | string | null = null, url: string | undefined;
  if (entries.length === 1 && !LABEL_KEYS.includes(entries[0][0].toLowerCase())) {
    label = entries[0][0];
    const v = entries[0][1];
    date = asDate(v);
    if (!date && typeof v === "string" && URL_RE.test(v)) url = v;
    else if (!date && (typeof v === "string" || typeof v === "number")) label = `${label}: ${v}`;
  } else {
    for (const [k, v] of entries) {
      const key = k.toLowerCase();
      if (!label && LABEL_KEYS.includes(key) && (typeof v === "string" || typeof v === "number")) label = String(v);
      else if (!date && DATE_KEYS.includes(key)) date = asDate(v);
      else if (!url && URL_KEYS.includes(key) && typeof v === "string" && URL_RE.test(v)) url = v;
    }
  }
  if (!label && url) label = displayUrl(url);
  if (!label) return <KeyValue items={entries.map(([k, v]) => ({ key: k, value: kvValue(v) }))} nested />;
  return (
    <span className="vx-kv-record">
      {url ? <Truncate text={label} href={url} /> : <Truncate text={label} mode={/\s/.test(label) ? "end" : "middle"} />}
      {date && <><span className="vx-kv-record-sep" aria-hidden>·</span><DateText value={date} /></>}
    </span>
  );
}

export function kvValue(value: unknown): ReactNode {
  if (value instanceof Date) return <DateText value={value} />;
  if (Array.isArray(value)) {
    const parts = value.filter((v) => !isEmptyValue(v));
    // A LIST OF RECORDS IS A LIST OF LINES, one block per item. An inline list item is a
    // shrink-to-fit box, and a table inside it (an inline-size container) has no width of its own,
    // so it collapsed to a sliver and printed its label one letter per line (founder, 2026-10-10,
    // a person page's `sources`). Scalars stay a comma-separated run.
    if (parts.some(isObj)) {
      return <ul className="vx-kv-items">{parts.map((v, i) => <li key={i} className="vx-kv-item">{isObj(v) ? kvRecordLine(v) : kvValue(v)}</li>)}</ul>;
    }
    return <span className="vx-kv-list">{parts.map((v, i) => <span key={i} className="vx-kv-list-item">{kvValue(v)}</span>)}</span>;
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (isObj(value)) {
    const items = Object.entries(value).filter(([, v]) => !isEmptyValue(v))
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

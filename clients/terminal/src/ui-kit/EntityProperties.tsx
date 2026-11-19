"use client";
import { parseDocument } from "yaml";
import { KeyValue, isEmptyValue, kvValue } from "./primitives/KeyValue";
import { Wikilink } from "./docLinks";
import { RelDate } from "./richMarkdown";
import { copyText } from "./ContextMenu";
import { useState, type ReactNode } from "react";

/** Parse entity properties independently of policy documents' scalar attribute contract. */
export function entityProperties(source: string): Record<string, unknown> | null {
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(source);
  if (!match) return null;
  try {
    const doc = parseDocument(match[1], { schema: "core", uniqueKeys: true });
    if (doc.errors.length) return null;
    const value = doc.toJS({ maxAliasCount: 50 });
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    // An entity declares its `type`; a page without one still shows its properties when it carries
    // the facts a reader looks for on a person or company (#1783).
    const keys = Object.keys(value);
    return typeof (value as { type?: unknown }).type === "string" || keys.some((k) => PERSONISH.test(k))
      ? value as Record<string, unknown> : null;
  } catch { return null; }
}

const PERSONISH = /^(role|company|organi[sz]ation|org|employer|email|e-mail|phone|last[_ -]?contact(ed)?|next[_ -]?step|status|owner|stage)$/i;
const LINKED = /^(company|organi[sz]ation|org|employer|account|parent|owner|project|reports[_ -]?to|manager)$/i;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ISO = /^\d{4}-\d{2}-\d{2}$/;
/** Shown in the page's own heading, not repeated as a property. */
const HIDDEN = /^(title)$/i;

function Copyable({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <span className="vx-copyable">
      <span className="vx-copyable-text">{text}</span>
      <button type="button" className="vx-copyable-btn" aria-label={`Copy ${text}`} title="Copy"
        onClick={() => { void copyText(text).then(() => { setDone(true); window.setTimeout(() => setDone(false), 1200); }); }}>
        {done ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

/** One property's value: a related page as its chip, an email to copy, a date relatively. */
function propertyValue(key: string, value: unknown): ReactNode {
  if (typeof value === "string") {
    const v = value.trim();
    const wiki = /^\[\[([^\]]+)\]\]$/.exec(v);
    if (wiki) return <Wikilink title={wiki[1]} />;
    if (EMAIL.test(v)) return <Copyable text={v} />;
    if (ISO.test(v)) return <RelDate iso={v} />;
    if (LINKED.test(key) && v && v.length <= 80 && !/\s{2,}/.test(v)) return <Wikilink title={v} />;
  }
  if (value instanceof Date && !Number.isNaN(value.getTime())) return <RelDate iso={value.toISOString().slice(0, 10)} />;
  return kvValue(value);
}

/** A page's properties, as the shared metadata table (guidelines §4.16). The table owns the
 *  layout — stacked below 360px of pane width, two columns above — and renders each value by its
 *  type, so nothing here breaks a word: this file used to set overflow-wrap to "anywhere" on both
 *  columns, which split a domain as "car eers" and a date as "2026-10- 09" in a 240px panel. */
export function EntityProperties({ source }: { source: string }) {
  const properties = entityProperties(source);
  if (!properties) return null;
  const items = Object.entries(properties).filter(([k, v]) => !isEmptyValue(v) && !HIDDEN.test(k))
    .map(([key, value]) => ({ key, value: propertyValue(key, value) }));
  return <section aria-label="Entity properties" data-entity-properties className="mb-5 pb-3 bd-b">
    <KeyValue items={items} />
  </section>;
}

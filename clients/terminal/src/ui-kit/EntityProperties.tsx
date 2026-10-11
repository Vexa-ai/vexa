"use client";
import { parseDocument } from "yaml";
import { KeyValue, isEmptyValue, kvValue } from "./primitives/KeyValue";

/** Parse entity properties independently of policy documents' scalar attribute contract. */
export function entityProperties(source: string): Record<string, unknown> | null {
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(source);
  if (!match) return null;
  try {
    const doc = parseDocument(match[1], { schema: "core", uniqueKeys: true });
    if (doc.errors.length) return null;
    const value = doc.toJS({ maxAliasCount: 50 });
    return value && typeof value === "object" && !Array.isArray(value) && typeof value.type === "string"
      ? value as Record<string, unknown> : null;
  } catch { return null; }
}

/** A page's properties, as the shared metadata table (guidelines §4.16). The table owns the
 *  layout — stacked below 360px of pane width, two columns above — and renders each value by its
 *  type, so nothing here breaks a word: this file used to set overflow-wrap to "anywhere" on both
 *  columns, which split a domain as "car eers" and a date as "2026-10- 09" in a 240px panel. */
export function EntityProperties({ source }: { source: string }) {
  const properties = entityProperties(source);
  if (!properties) return null;
  const items = Object.entries(properties).filter(([, v]) => !isEmptyValue(v))
    .map(([key, value]) => ({ key, value: kvValue(value) }));
  return <section aria-label="Entity properties" data-entity-properties className="mb-5 pb-3 bd-b">
    <KeyValue items={items} />
  </section>;
}

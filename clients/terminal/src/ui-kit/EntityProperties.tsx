"use client";
import { parseDocument } from "yaml";
import type { ReactNode } from "react";

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

function valueView(value: unknown): ReactNode {
  if (Array.isArray(value)) return <span style={{ display: "inline-flex", flexWrap: "wrap", gap: "3px 10px" }}>
    {value.map((v, i) => <span key={i}>{valueView(v)}</span>)}
  </span>;
  if (value && typeof value === "object") return <span style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(value, null, 2)}</span>;
  const text = String(value ?? "");
  if (/^https?:\/\/[^\s]+$/i.test(text)) return <a href={text} target="_blank" rel="noopener noreferrer" style={{ color: "var(--blue)", overflowWrap: "anywhere" }}>{text}</a>;
  return text;
}

export function EntityProperties({ source }: { source: string }) {
  const properties = entityProperties(source);
  if (!properties) return null;
  const rows = Object.entries(properties).filter(([, v]) => v !== null && v !== "" && v !== undefined);
  return <section aria-label="Entity properties" data-entity-properties style={{ marginBottom: 20, paddingBottom: 14, borderBottom: "1px solid var(--line)" }}>
    <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "minmax(80px, 120px) minmax(0, 1fr)", gap: "5px 14px", fontSize: 12, lineHeight: 1.5 }}>
      {rows.map(([key, value]) => <div key={key} style={{ display: "contents" }}>
        <dt style={{ color: "var(--t3)", overflowWrap: "anywhere" }}>{key.replaceAll("_", " ")}</dt>
        <dd style={{ margin: 0, color: "var(--t2)", overflowWrap: "anywhere" }}>{valueView(value)}</dd>
      </div>)}
    </dl>
  </section>;
}

"use client";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { type as ty } from "../minutes/tokens";
import { CardRecord, CardLayout, CardField, fieldLabel, formatValue } from "./CrmCard";
function text(value: unknown) {
  return String(value ?? "").replace(/[\\`*_{}\[\]<>()#!|]/g, "\\$&").replace(/[\r\n]+/g, " ");
}
export function tableDocument(records: CardRecord[], objectType: string, layout: CardLayout | null): string {
  const titleField = layout?.title_field || "Name";
  const available = new Set(records.flatMap(r => Object.keys(r.fields)));
  const configured = layout?.sections.flatMap(s => s.fields) || [];
  const fallback = [...available].filter(f => !/^(Id|Name|Subject|.*Id|IsDeleted|SystemModstamp|CreatedDate|LastModifiedDate)$/.test(f)).map(field => ({field}));
  const seen = new Set([titleField]);
  const columns: CardField[] = (configured.length ? configured : fallback).filter(f => {
    if (!available.has(f.field) || seen.has(f.field)) return false;
    seen.add(f.field); return true;
  }).slice(0, 6);
  const lines = [`# ${text(fieldLabel(objectType))}`, ""];
  if (!records.length) return lines.concat("No records match this view.").join("\n");
  lines.push(`| Record | ${columns.map(f => text(f.label || fieldLabel(f.field))).join(" | ")}${columns.length ? " |" : ""}`);
  // A single Record column remains a valid GFM table.
  if (!columns.length) lines[lines.length - 1] = "| Record |";
  lines.push(`| --- |${columns.map(() => " --- |").join("")}`);
  for (const record of records) {
    const label = record.fields[titleField] || record.fields.Name || record.fields.Subject || record.object_type;
    const cells = [`[${text(label)}](/crm?record=${encodeURIComponent(record.id)})`, ...columns.map(f => {
      const links=record.links?.filter(link=>link.field===f.field) || [];
      return links.length ? links.map(link=>`[${text(link.label)}](/crm?record=${encodeURIComponent(link.record_id)})`).join(", ") : text(formatValue(record.fields[f.field], f));
    })];
    lines.push(`| ${cells.join(" | ")} |`);
  }
  return lines.join("\n");
}
export function CrmTable({records, objectType, layout, onRead, busy}: {records: CardRecord[]; objectType: string; layout: CardLayout | null; onRead: (id: string) => void; busy: boolean}) {
  return <article data-crm-table style={{...ty.body,lineHeight:1.6,color:"var(--t1)",overflowX:"auto"}} onClickCapture={event => {
    const href = (event.target as Element).closest("a[href]")?.getAttribute("href");
    if (!href?.startsWith("/crm?") || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const id = new URL(href,window.location.origin).searchParams.get("record");
    if (!id) return;
    event.preventDefault();event.stopPropagation();if (!busy) onRead(id);
  }}><MdxDoc>{tableDocument(records,objectType,layout)}</MdxDoc></article>;
}

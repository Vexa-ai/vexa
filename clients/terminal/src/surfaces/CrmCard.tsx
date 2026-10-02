"use client";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { type as ty } from "../minutes/tokens";
export type CardField = {field: string; label?: string; format?: string; currency?: string};
export type CardLayout = {title_field?: string; sections: {title: string; fields: CardField[]}[]; show_narrative?: boolean; show_related?: boolean};
export type CardRecord = {id: string; object_type: string; revision: number; sources?: {system: string; source_id: string}[]; fields: Record<string, unknown>; narrative: string | null; links?: {field: string; record_id: string; label: string}[]; card?: {version: number; layout: CardLayout | null}};
export function fieldLabel(name: string) {return name.replace(/__c$/, "").replace(/_/g," ").replace(/([a-z])([A-Z])/g,"$1 $2").replace(/Id$/, "").trim();}
const technical = /^(Id|OwnerId|CreatedById|LastModifiedById|RecordTypeId|IsDeleted|SystemModstamp|CreatedDate|LastModifiedDate)$/;
function defaultLayout(record: CardRecord): CardLayout {
  const fields = Object.keys(record.fields).filter(f => !technical.test(f) && !['Name','Subject','Description'].includes(f) && record.fields[f] !== null);
  return {title_field: 'Name' in record.fields ? 'Name' : 'Subject', show_narrative: true, show_related: true, sections: [
    {title: 'Overview', fields: fields.slice(0,12).map(field => ({field, format: /Amount|AUM/.test(field) ? 'currency' : /Date/.test(field) ? 'date' : /Probability/.test(field) ? 'percent' : /Stage|Status/.test(field) ? 'badge' : typeof record.fields[field] === 'number' ? 'number' : 'text'}))},
    ...('Description' in record.fields ? [{title: 'About', fields: [{field:'Description',label:'',format:'markdown'}]}] : []),
  ]};
}
export function formatValue(value: unknown, field: CardField): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'string' && ['number','currency','percent'].includes(field.format || '') && /^-?\d+(\.\d+)?$/.test(value.trim())) value = Number(value);
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number' && Number.isFinite(value)) {
    if (field.format === 'currency') {try {return new Intl.NumberFormat('en-US',{style:'currency',currency:field.currency || 'USD',minimumFractionDigits:0,maximumFractionDigits:2}).format(value);} catch {return String(value);}}
    if (field.format === 'percent') return `${new Intl.NumberFormat('en-US',{maximumFractionDigits:2}).format(value)}%`;
    return new Intl.NumberFormat('en-US',{maximumFractionDigits:4}).format(value);
  }
  if (field.format === 'date' && typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value)) {
    const date = new Date(value); if (!Number.isNaN(date.valueOf())) return new Intl.DateTimeFormat('en-US',{dateStyle:'medium',timeZone:'UTC'}).format(date);
  }
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

/** Treat field text as text; only fields explicitly configured as Markdown carry markup. */
function prose(value: unknown): string {
  return String(value ?? '').replace(/[\\`*_{}\[\]<>()#!|]/g, '\\$&').replace(/[\r\n]+/g, ' ');
}
export function cardDocument(record: CardRecord): string {
  const layout = record.card?.layout || defaultLayout(record);
  const title = record.fields[layout.title_field || 'Name'] || record.fields.Name || record.fields.Subject || record.object_type;
  const body = [`# ${prose(title)}`];
  for (const section of layout.sections) {
    const fields = section.fields.filter(f => Object.hasOwn(record.fields,f.field));
    if (!fields.length) continue;
    if (section.title) body.push(`## ${prose(section.title)}`);
    for (const field of fields) {
      const label = field.label || (field.format === 'markdown' ? '' : fieldLabel(field.field));
      const links = record.links?.filter(l => l.field === field.field) || [];
      const value = links.length
        ? links.map(l => `[${prose(l.label)}](/crm?record=${encodeURIComponent(l.record_id)})`).join(', ')
        : field.format === 'markdown' ? String(record.fields[field.field] ?? '') : prose(formatValue(record.fields[field.field],field));
      body.push(label ? `**${prose(label)}:** ${value}` : value);
    }
  }
  if (layout.show_narrative !== false && record.narrative) body.push('## Notes',record.narrative);
  const links = record.links?.filter(l => !['CreatedById','LastModifiedById','RecordTypeId'].includes(l.field)) || [];
  if (layout.show_related !== false && links.length) {
    body.push('## Related records',links.map(l => `- **${prose(fieldLabel(l.field))}:** [${prose(l.label)}](/crm?record=${encodeURIComponent(l.record_id)})`).join('\n'));
  }
  return body.join('\n\n');
}
export function CrmCard({record, onRead, busy}: {record: CardRecord; onRead: (id:string)=>void; busy: boolean}) {
  return <article data-crm-document style={{...ty.body,lineHeight:1.6,color:'var(--t1)',overflowWrap:'anywhere'}} onClickCapture={event => {
    const anchor = (event.target as Element).closest('a[href]');
    const href = anchor?.getAttribute('href');
    if (!href?.startsWith('/crm?') || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const id = new URL(href,window.location.origin).searchParams.get('record');
    if (!id) return;
    event.preventDefault();event.stopPropagation();if (!busy) onRead(id);
  }}>
    <div style={ty.meta}>{fieldLabel(record.object_type)} · Revision {record.revision}</div>
    <MdxDoc>{cardDocument(record)}</MdxDoc>
    <details style={{...ty.meta,margin:'12px 0'}}><summary style={{cursor:'pointer'}}>All fields · {Object.keys(record.fields).length}</summary>
      <MdxDoc>{Object.entries(record.fields).map(([name,value]) => `**${prose(name)}:** ${prose(formatValue(value,{field:name}))}`).join('\n\n')}</MdxDoc>
      {record.sources?.map(source=><p key={source.system+source.source_id}>Source: {source.system} · {source.source_id}</p>)}
    </details>
  </article>;
}

"use client";
import { Markdown } from "../ui-kit/Markdown";
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
export function CrmCard({record, onRead, busy}: {record: CardRecord; onRead: (id:string)=>void; busy: boolean}) {
  const layout = record.card?.layout || defaultLayout(record);
  const related = record.links?.filter(link => !['CreatedById','LastModifiedById','RecordTypeId'].includes(link.field)) || [];
  const linkStyle = {color:'var(--blue)', background:'none',border:0,padding:0,cursor:'pointer',textAlign:'left' as const,font:'inherit'};
  function renderField(field: CardField) {
    const links = record.links?.filter(l => l.field === field.field) || [];
    if (links.length) return links.map(link => <button key={link.record_id} style={linkStyle} disabled={busy} onClick={()=>onRead(link.record_id)}>{link.label}</button>);
    const value = formatValue(record.fields[field.field],field);
    if (field.format === 'markdown') return <Markdown>{value}</Markdown>;
    if (field.format === 'badge') return <span style={{display:'inline-block',border:'1px solid var(--line)',background:'var(--panel2)',borderRadius:20,padding:'2px 9px',fontSize:12}}>{value}</span>;
    return value;
  }
  return <article style={{fontSize:14,lineHeight:1.6,overflowWrap:'anywhere'}}>
    <p style={{fontSize:11,textTransform:'uppercase',letterSpacing:'.08em',color:'var(--t3)',margin:'0 0 6px'}}>{fieldLabel(record.object_type)} · Revision {record.revision}</p>
    <h2 style={{fontSize:24,lineHeight:1.25,fontWeight:600,margin:'0 0 24px'}}>{String(record.fields[layout.title_field || 'Name'] || record.fields.Name || record.fields.Subject || record.object_type)}</h2>
    {layout.sections.map((section,i) => {
      const fields = section.fields.filter(f => Object.hasOwn(record.fields,f.field));
      if (!fields.length) return null;
      return <section key={i} style={{marginBottom:24}}>
        {section.title && <h3 style={{fontSize:15,fontWeight:600,margin:'0 0 10px'}}>{section.title}</h3>}
        <dl style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,180px),1fr))',gap:'14px 24px',margin:0}}>{fields.map((field,j)=><div key={j} style={{gridColumn:field.format==='markdown'?'1 / -1':undefined}}>
          <dt style={{fontSize:12,color:'var(--t3)',marginBottom:3}}>{field.label === undefined ? fieldLabel(field.field) : field.label || (field.format === 'markdown' ? '' : fieldLabel(field.field))}</dt>
          <dd style={{margin:0}}>{renderField(field)}</dd>
        </div>)}</dl>
      </section>;
    })}
    {layout.show_narrative !== false && record.narrative && <section style={{marginBottom:24}}><h3 style={{fontSize:15}}>Notes</h3><Markdown>{record.narrative}</Markdown></section>}
    {layout.show_related !== false && !!related.length && <nav aria-label="Related records" style={{marginBottom:24}}><h3 style={{fontSize:15}}>Related records</h3><div style={{display:'flex',flexWrap:'wrap',gap:8}}>{related.map(link=><button key={link.field+link.record_id} disabled={busy} onClick={()=>onRead(link.record_id)} style={{...linkStyle,border:'1px solid var(--line)',borderRadius:8,padding:'8px 12px'}}><span style={{display:'block',fontSize:11,color:'var(--t3)'}}>{fieldLabel(link.field)}</span>{link.label}</button>)}</div></nav>}
    <details style={{margin:'20px 0',color:'var(--t2)',fontSize:12}}><summary style={{cursor:'pointer'}}>All fields · {Object.keys(record.fields).length}</summary><dl>{Object.entries(record.fields).map(([name,value])=><div key={name} style={{padding:'6px 0',borderBottom:'1px solid var(--line)'}}><dt>{name}</dt><dd style={{margin:0}}>{formatValue(value,{field:name})}</dd></div>)}</dl>{record.sources?.map(source => <p key={source.system+source.source_id}>Source: {source.system} · {source.source_id}</p>)}</details>
  </article>;
}

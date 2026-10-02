"use client";
import { T, surface, type as ty } from "./tokens";
import { CrmView } from "../surfaces/crm";
export function CrmPanel({ recordId, onClose, onCollapse }: { recordId: string; onClose: () => void; onCollapse: () => void }) {
  const query = new URL(recordId.startsWith("/crm?") ? recordId : `/crm?record=${encodeURIComponent(recordId)}`, "https://local").searchParams;
  return <aside aria-label="CRM record panel" style={{ gridRow: "1 / span 2", gridColumn: 3, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", background: surface.pages, borderLeft: "1px solid var(--line)" }}>
    <header style={{ display: "flex", alignItems: "center", height:T.headerH,flex:"none",gap:12,padding:"0 20px",...ty.body, borderBottom: "1px solid var(--line)" }}>
      <strong style={{ ...ty.title, flex: 1 }}>{query.has("object") ? "CRM table" : "CRM record"}</strong>
      <button style={{...ty.control,background:"none",border:0,color:"var(--t2)",cursor:"pointer"}} onClick={onClose}>Back to pages</button>
      <button style={{...ty.control,background:"none",border:0,color:"var(--t2)",cursor:"pointer"}} aria-label="Hide CRM panel" onClick={onCollapse}>Hide</button>
    </header>
    <div style={{ overflow: "auto", flex: 1, minHeight: 0 }}><CrmView key={recordId} initialRecord={query.get("record") || ""} initialObject={query.get("object") || ""} initialFilters={query.get("filters") || "{}"} embedded /></div>
  </aside>;
}

"use client";
import { CrmView } from "../surfaces/crm";
export function CrmPanel({ recordId, onClose, onCollapse }: { recordId: string; onClose: () => void; onCollapse: () => void }) {
  return <aside aria-label="CRM record panel" style={{ gridRow: "1 / span 2", gridColumn: 3, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", background: "var(--panel)", borderLeft: "1px solid var(--line)" }}>
    <header style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 18px", borderBottom: "1px solid var(--line)" }}>
      <strong style={{ flex: 1 }}>CRM record</strong>
      <button onClick={onClose}>Back to pages</button>
      <button aria-label="Hide CRM panel" onClick={onCollapse}>Hide</button>
    </header>
    <div style={{ overflow: "auto", flex: 1, minHeight: 0 }}><CrmView key={recordId} initialRecord={recordId} embedded /></div>
  </aside>;
}

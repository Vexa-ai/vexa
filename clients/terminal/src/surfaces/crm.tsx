"use client";
import { useEffect, useState } from "react";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { CrmTable } from "./CrmTable";
import { CrmCard, CardLayout } from "./CrmCard";
import { type as ty } from "../minutes/tokens";
import { ASK_CHAT_EVENT } from "../canvas/actions";

type Proposal = { narrative?: string | null; id: string; base_revision: number; fields: Record<string, unknown>; reason: string | null; evidence: Record<string, unknown>[] };
type RecordView = { card?: {version: number; layout: CardLayout | null}; id: string; object_type: string; revision: number; fields: Record<string, unknown>; narrative: string | null; actions?: { can_review: boolean }; proposals?: Proposal[]; links?: { field: string; record_id: string; label: string }[]; sources?: { system: string; source_id: string }[] };
type Revision = { revision: number; created_at: string; reason: string | null; after: RecordView };
async function call(operation: string, args: Record<string, unknown>) {
  const response = await fetch("/api/crm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ operation, ...args }) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `CRM request failed (${response.status})`);
  return data;
}
export function CrmView({ initialName = "", initialRecord = "", initialObject = "", initialFilters = "{}", embedded = false }: { initialName?: string; initialRecord?: string; initialObject?: string; initialFilters?: string; embedded?: boolean }) {
  const [objects, setObjects] = useState<string[]>([]);
  const [kind, setKind] = useState(initialObject || "Account");
  const [tableLayout, setTableLayout] = useState<CardLayout | null>(null);
  const [listed, setListed] = useState(false);
  const [records, setRecords] = useState<RecordView[]>([]);
  const [selected, setSelected] = useState<RecordView | null>(null);
  const [history, setHistory] = useState<Revision[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [offset, setOffset] = useState(0);
  const [next, setNext] = useState<number | null>(null);
  async function run(work: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : "CRM request failed"); }
    finally { setBusy(false); }
  }
  async function loadSchema() {
    setSelected(null); setRecords([]); setListed(false); setHistory([]);
    const data = await call("describe", {});
    setObjects(data.objects.map((o: { object_type: string }) => o.object_type).sort());
  }
  async function list(at = 0, objectType = kind) {
    const filters = objectType === initialObject ? JSON.parse(initialFilters) : {};
    if (!filters || typeof filters !== "object" || Array.isArray(filters)) throw new Error("Invalid CRM table filters");
    const data = await call("search", { object_type: objectType, filters, limit: 20, offset: at });
    setTableLayout(data.card?.layout || null); setListed(true); setKind(objectType);
    setRecords(data.records); setOffset(at); setNext(data.next_offset); setSelected(null); setHistory([]);
  }
  async function read(id: string) {
    const data = await call("read", { record_id: id });
    if (!embedded) window.history.replaceState(null, "", `/crm?${new URLSearchParams({ record: id })}`);
    setSelected(data); setHistory([]);
  }
  useEffect(() => { void run(async () => { await loadSchema(); if (initialRecord) await read(initialRecord); else if (initialName) {
      const data=await call("resolve",{name:initialName});
      if (data.records.length===1) await read(data.records[0].id);
      else {setRecords(data.records);setKind(`Matches for ${initialName}`);setListed(true);setNext(null);}
    } else if (initialObject) await list(); }); }, []); // initial deployment selection only
  const button = { ...ty.control, padding: "8px 12px", background: "var(--panel)", color: "var(--t1)", border: "1px solid var(--line)", borderRadius: 6 };
  const input = { ...button, minWidth: 0 };
  return <main style={{ maxWidth: 1200, margin: "0 auto", padding: embedded ? "18px 20px 40px" : 32, color: "var(--t1)" }}>
    {!embedded && <><header style={{ display: "flex", alignItems: "center", gap: 24, marginBottom: 24 }}><a href="/">← Minutes</a><h1>CRM</h1><span>Preview</span></header>
    <form onSubmit={e => { e.preventDefault(); void run(loadSchema); }} style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      <button style={button} disabled={busy}>Refresh</button>
      <select aria-label="Record type" style={input} value={kind} onChange={e => { setKind(e.target.value); setRecords([]); setListed(false); setSelected(null); }}>
        {!objects.includes(kind) && <option>{kind}</option>}{objects.map(name => <option key={name}>{name}</option>)}
      </select>
      <button type="button" style={button} disabled={busy || !objects.length} onClick={() => void run(() => list())}>Browse records</button>
    </form></>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div style={{ display: "grid", gridTemplateColumns: embedded ? "1fr" : "repeat(auto-fit, minmax(min(100%, 350px), 1fr))", gap: 28, marginTop: embedded ? 0 : 24 }}>
      {!selected && listed && <section aria-label="Records" style={{minWidth:0}}><CrmTable records={records} objectType={kind} layout={tableLayout} busy={busy} onRead={id => void run(() => read(id))} />
      <div style={{ display: "flex", gap: 8 }}><button style={button} disabled={busy || offset === 0} onClick={() => void run(() => list(Math.max(0, offset - 20)))}>Previous</button><button style={button} disabled={busy || next === null} onClick={() => void run(() => list(next!))}>Next</button></div></section>}
      {selected && <section aria-label="Record details">{embedded && !listed && <button style={button} disabled={busy} onClick={() => void run(() => list(0, selected.object_type))}>Browse {selected.object_type}</button>}{listed && <button style={button} disabled={busy} onClick={() => {setSelected(null);setHistory([]);}}>← Back to table</button>}<CrmCard record={selected} busy={busy} onRead={id => void run(() => read(id))} />
        {embedded && <div style={{display:"flex",gap:12,margin:"12px 0"}}>{["Edit description", "Update record", "Configure card"].map(action => <button key={action} style={{...ty.control,background:"none",border:0,padding:0,color:"var(--t2)",cursor:"pointer"}} disabled={busy} onClick={() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT,{detail:{
          display: action,
          prompt: action === "Edit description"
            ? `Help me edit the Markdown description of CRM record ${selected.id}. Read its current narrative and revision through crm_read, ask what I want changed, then use crm_change with narrative and expected_revision. Preserve structured fields. Link CRM records using stable returned hrefs so links create graph relationships and backlinks; use workspace links for reusable knowledge. Do not copy the description into workspace files.`
            : action === "Configure card"
            ? `Help me configure the shared CRM card layout for object type ${selected.object_type}. Read crm_configure first, ask what I want changed, then preserve other settings and save with expected_version. Do not change record data.`
            : `Help me update CRM record ${selected.id}. Read it through MCP, ask what I want changed, and propose the change with its current revision and evidence. Do not write account data to workspace files.`
        }}))}>{action}</button>)}</div>}
        {selected.proposals?.map(item => <article key={item.id} style={{ border: "1px solid var(--line)", padding: 12, margin: "12px 0" }}><h3>Pending proposal</h3><p>Based on revision {item.base_revision}</p>{Object.entries(item.fields).map(([name, proposed]) => <p key={name}><strong>{name}</strong><br />Current: {String(selected.fields[name] ?? "—")}<br />Proposed: {String(proposed ?? "—")}</p>)}{item.narrative != null && <><h4>Proposed description</h4><MdxDoc>{item.narrative || "(Empty description)"}</MdxDoc></>}{item.reason && <p>{item.reason}</p>}{item.evidence.length > 0 && <details><summary>Source evidence</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(item.evidence, null, 2)}</pre></details>}{selected.actions?.can_review && <div>{[true, false].map(accept => <button key={String(accept)} style={button} disabled={busy} onClick={() => void run(async () => { await call("review", { proposal_id: item.id, accept }); await read(selected.id); setNotice(accept ? "Proposal accepted." : "Proposal rejected."); })}>{accept ? "Accept" : "Reject"}</button>)}</div>}</article>)}
        <button style={button} disabled={busy} onClick={() => void run(async () => { const data = await call("history", { record_id: selected.id }); setHistory(data.revisions); })}>Show history</button>
        {history.map(revision => <details key={revision.revision}><summary>Revision {revision.revision} · {revision.created_at}</summary><p>{revision.reason}</p>{revision.after.narrative != null && <><h4>Description</h4><MdxDoc>{revision.after.narrative || "(Empty description)"}</MdxDoc></>}<pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(revision.after.fields, null, 2)}</pre></details>)}
      </section>}
    </div>
  </main>;
}

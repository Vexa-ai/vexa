"use client";
import { useEffect, useState } from "react";
import { Markdown } from "../ui-kit/Markdown";

type Proposal = { id: string; base_revision: number; fields: Record<string, unknown>; reason: string | null; evidence: Record<string, unknown>[] };
type RecordView = { id: string; object_type: string; revision: number; fields: Record<string, unknown>; narrative: string | null; actions?: { can_review: boolean }; proposals?: Proposal[]; links?: { field: string; record_id: string; label: string }[]; sources?: { system: string; source_id: string }[] };
type Revision = { revision: number; created_at: string; reason: string | null; after: RecordView };
async function call(operation: string, args: Record<string, unknown>) {
  const response = await fetch("/api/crm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ operation, ...args }) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `CRM request failed (${response.status})`);
  return data;
}
export function CrmView({ initialRecord = "", embedded = false }: { initialRecord?: string; embedded?: boolean }) {
  const [objects, setObjects] = useState<string[]>([]);
  const [kind, setKind] = useState("Account");
  const [records, setRecords] = useState<RecordView[]>([]);
  const [selected, setSelected] = useState<RecordView | null>(null);
  const [history, setHistory] = useState<Revision[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [field, setField] = useState("NextStep");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [proposal, setProposal] = useState("");
  const [offset, setOffset] = useState(0);
  const [next, setNext] = useState<number | null>(null);
  async function run(work: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : "CRM request failed"); }
    finally { setBusy(false); }
  }
  async function loadSchema() {
    setSelected(null); setRecords([]); setHistory([]); setProposal("");
    const data = await call("describe", {});
    setObjects(data.objects.map((o: { object_type: string }) => o.object_type).sort());
  }
  async function list(at = 0) {
    const data = await call("search", { object_type: kind, limit: 20, offset: at });
    setRecords(data.records); setOffset(at); setNext(data.next_offset); setSelected(null); setHistory([]); setProposal("");
  }
  async function read(id: string) {
    const data = await call("read", { record_id: id });
    if (!embedded) window.history.replaceState(null, "", `/crm?${new URLSearchParams({ record: id })}`);
    setSelected(data); setKind(data.object_type); setHistory([]); setField("NextStep" in data.fields ? "NextStep" : Object.keys(data.fields)[0] || ""); setValue(""); setReason(""); setProposal("");
  }
  useEffect(() => { void run(async () => { await loadSchema(); if (initialRecord) await read(initialRecord); }); }, []); // initial deployment selection only
  const button = { padding: "8px 12px", background: "var(--panel)", color: "var(--t1)", border: "1px solid var(--line)", borderRadius: 6 };
  const input = { ...button, minWidth: 0 };
  return <main style={{ maxWidth: 1200, margin: "0 auto", padding: 24, color: "var(--t1)" }}>
    {!embedded && <><header style={{ display: "flex", alignItems: "center", gap: 24, marginBottom: 24 }}><a href="/">← Minutes</a><h1>CRM</h1><span>Preview</span></header>
    <form onSubmit={e => { e.preventDefault(); void run(loadSchema); }} style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      <button style={button} disabled={busy}>Refresh</button>
      <select aria-label="Record type" style={input} value={kind} onChange={e => { setKind(e.target.value); setRecords([]); setSelected(null); setProposal(""); }}>
        {!objects.includes(kind) && <option>{kind}</option>}{objects.map(name => <option key={name}>{name}</option>)}
      </select>
      <button type="button" style={button} disabled={busy || !objects.length} onClick={() => void run(() => list())}>Browse records</button>
    </form></>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div style={{ display: "grid", gridTemplateColumns: embedded ? "1fr" : "repeat(auto-fit, minmax(min(100%, 350px), 1fr))", gap: 28, marginTop: 24 }}>
      {!embedded && <section aria-label="Records"><h2>{kind}</h2>{records.map(record => <button key={record.id} style={{ ...button, display: "block", width: "100%", textAlign: "left", marginBottom: 6 }} disabled={busy} onClick={() => void run(() => read(record.id))}>
        {String(record.fields.Name || record.fields.Subject || record.fields.External_Id__c || record.id)}
      </button>)}
      <div style={{ display: "flex", gap: 8 }}><button style={button} disabled={busy || offset === 0} onClick={() => void run(() => list(Math.max(0, offset - 20)))}>Previous</button><button style={button} disabled={busy || next === null} onClick={() => void run(() => list(next!))}>Next</button></div></section>}
      {selected && <section aria-label="Record details"><h2>{String(selected.fields.Name || selected.fields.Subject || selected.object_type)}</h2><p>Revision {selected.revision}</p>
        <dl>{Object.entries(selected.fields).map(([key, value]) => <div key={key} style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}><dt style={{ color: "var(--t3)" }}>{key}</dt><dd style={{ margin: 0, overflowWrap: "anywhere" }}>{typeof value === "object" ? JSON.stringify(value) : String(value ?? "—")}</dd></div>)}</dl>
        {!!selected.links?.length && <nav aria-label="Related records"><h3>Related records</h3>{selected.links.map(link => <button key={link.field + link.record_id} style={button} disabled={busy} onClick={() => void run(() => read(link.record_id))}>{link.field}: {link.label}</button>)}</nav>}
        {selected.sources?.map(source => <p key={source.system + source.source_id}>Source: {source.system} · {source.source_id}</p>)}
        {selected.narrative && <Markdown>{selected.narrative}</Markdown>}
        <h3>Propose a change</h3><label>Field<select aria-label="Field to change" style={{ ...input, width: "100%" }} value={field} onChange={e => { setField(e.target.value); setValue(""); }}>{Object.keys(selected.fields).map(name => <option key={name}>{name}</option>)}</select></label>
        <label>New value<textarea aria-label="New value" style={{ ...input, width: "100%", minHeight: 80 }} value={value} onChange={e => setValue(e.target.value)} /></label>
        <label>Reason<input aria-label="Reason" style={{ ...input, width: "100%" }} value={reason} onChange={e => setReason(e.target.value)} /></label>
        <button style={button} disabled={busy || !reason} onClick={() => void run(async () => { let typed: unknown = value; const previous = selected.fields[field]; if (typeof previous === "number") { typed = Number(value); if (!value.trim() || !Number.isFinite(typed)) throw new Error("Enter a valid number"); } else if (typeof previous === "boolean") { if (!["true", "false"].includes(value)) throw new Error("Enter true or false"); typed = value === "true"; } const fields = { [field]: typed }; const data = await call("change", { action: "propose", record_id: selected.id, expected_revision: selected.revision, fields, reason }); await read(selected.id); setProposal(data.proposal_id); setNotice("Proposal saved for review. The record has not changed."); })}>Submit proposal</button>
        {selected.proposals?.map(item => <article key={item.id} style={{ border: "1px solid var(--line)", padding: 12, margin: "12px 0" }}><h3>Pending proposal</h3><p>Based on revision {item.base_revision}</p>{Object.entries(item.fields).map(([name, proposed]) => <p key={name}><strong>{name}</strong><br />Current: {String(selected.fields[name] ?? "—")}<br />Proposed: {String(proposed ?? "—")}</p>)}{item.reason && <p>{item.reason}</p>}{item.evidence.length > 0 && <details><summary>Source evidence</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(item.evidence, null, 2)}</pre></details>}{selected.actions?.can_review && <div>{[true, false].map(accept => <button key={String(accept)} style={button} disabled={busy} onClick={() => void run(async () => { await call("review", { proposal_id: item.id, accept }); await read(selected.id); setNotice(accept ? "Proposal accepted." : "Proposal rejected."); })}>{accept ? "Accept" : "Reject"}</button>)}</div>}</article>)}
        <button style={button} disabled={busy} onClick={() => void run(async () => { const data = await call("history", { record_id: selected.id }); setHistory(data.revisions); })}>Show history</button>
        {history.map(revision => <details key={revision.revision}><summary>Revision {revision.revision} · {revision.created_at}</summary><p>{revision.reason}</p><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(revision.after.fields, null, 2)}</pre></details>)}
      </section>}
    </div>
  </main>;
}

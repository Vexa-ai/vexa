"use client";
/** The CRM record page (ADR-0047 d8) — the one CRM screen. Everything else about the CRM is the
 *  agent's: a person asks in the chat, the agent acts with the crm_* tools. Here a person sees a
 *  record's fields, the pages that mention it (only from workspaces they are a member of — the
 *  server omits the rest), its history, and the changes waiting for them, which they accept or
 *  reject with their own credential (an agent cannot). */
import { useCallback, useEffect, useState } from "react";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { useFeatureAnswer } from "../platform/features";
import { LinkedProfile } from "./profile/PublicProfile";
import "./crmRecord.css";

export type CrmProposal = { id: string; base_revision: number; author_kind?: string; fields: Record<string, unknown>;
  narrative?: string | null; reason: string | null; evidence: Record<string, unknown>[] };
export type CrmRecord = { id: string; object_type: string; revision: number; fields: Record<string, unknown>;
  narrative: string | null; mentioned_in?: { workspace: string; page: string }[]; bound_to?: string[];
  links?: { field: string; record_id: string; label: string }[];
  actions?: { can_review: boolean }; proposals?: CrmProposal[] };
export type CrmRevision = { revision: number; created_at: string; reason: string | null; after: { fields: Record<string, unknown> } };

export async function crmCall(operation: "read" | "history" | "review", args: Record<string, unknown>) {
  const response = await fetch("/api/crm", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operation, ...args }) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const why = typeof data.detail === "string" ? data.detail : typeof data.reason === "string" ? data.reason : "";
    throw new Error(response.status === 404 ? (why === "Not Found" ? "This deployment runs no CRM." : "This record does not exist, or you cannot see it.") : why || `The CRM answered ${response.status}`);
  }
  return data;
}

export function recordTitle(record: CrmRecord): string {
  const f = record.fields;
  return String(f.Name || f.name || f.Subject || [f.FirstName, f.LastName].filter(Boolean).join(" ") || record.object_type);
}

const SLUG = /^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$/;
/** Where each backlink opens: the workspace page at its route (`/w/<workspace>/<path>`). Spelled
 *  here rather than imported from app/, which imports this surface: the folders stay acyclic. A
 *  segment that could climb out (`.`, `..`, empty) opens the workspace instead of the page. */
export function mentionHref(m: { workspace: string; page: string }): string {
  if (!SLUG.test(m.workspace)) return "/";
  const segs = m.page.split("/").filter(Boolean);
  if (!segs.length || segs.some(s => s === "." || s === "..")) return `/w/${m.workspace}`;
  return `/w/${m.workspace}/${segs.map(encodeURIComponent).join("/")}`;
}

export function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/** While `crm` is switched off (`platform/features`) the page says so and reads nothing. */
export function CrmRecordPage(p: { recordId: string }) {
  const on = useFeatureAnswer("crm");
  if (on === undefined) return <main className="crm-page" aria-busy="true" />;
  if (!on) return <main className="crm-page" data-feature-off="crm"><p className="crm-note">The CRM is not switched on here.</p></main>;
  return <CrmRecordBody {...p} />;
}

function CrmRecordBody({ recordId }: { recordId: string }) {
  const [record, setRecord] = useState<CrmRecord | null>(null);
  const [history, setHistory] = useState<CrmRevision[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setRecord(await crmCall("read", { record_id: recordId }));
  }, [recordId]);

  const run = useCallback(async (work: () => Promise<void>) => {
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : "The CRM request failed"); }
    finally { setBusy(false); }
  }, []);

  useEffect(() => { if (recordId) void run(load); }, [recordId, load, run]);

  if (!recordId) return <main className="crm-page"><p className="crm-note">No record named. Ask the chat to find one.</p></main>;
  return <main className="crm-page">
    <div className="crm-meta"><a href="/">← Back</a></div>
    {error && <p role="alert" className="crm-alert">{error}</p>}
    {notice && <p role="status" className="crm-note">{notice}</p>}
    {record && <>
      <div className="crm-meta">{record.object_type} · revision {record.revision}</div>
      <h1 className="crm-title">{recordTitle(record)}</h1>

      <section className="crm-section" aria-label="Fields">
        <h2>Fields</h2>
        <dl className="crm-fields">
          {Object.entries(record.fields).map(([name, value]) => <div key={name} style={{ display: "contents" }}>
            <dt>{name}</dt><dd>{display(value)}</dd></div>)}
        </dl>
        {record.narrative && <div className="crm-section"><MdxDoc>{record.narrative}</MdxDoc></div>}
      </section>

      {/* R4 (`public_profiles`): the person's own public profile, live, beside this org's notes */}
      <LinkedProfile recordId={record.id} ownNotes={record.narrative} />

      <section className="crm-section" aria-label="Mentioned in">
        <h2>Mentioned in</h2>
        {record.mentioned_in && record.mentioned_in.length
          ? <ul className="crm-list">{record.mentioned_in.map(m => <li key={m.workspace + "/" + m.page}>
              <a href={mentionHref(m)}>{m.page}</a> <span className="crm-meta">· {m.workspace}</span></li>)}</ul>
          : <p className="crm-note">No page in your workspaces links this record.</p>}
      </section>

      {!!record.proposals?.length && <section className="crm-section" aria-label="Proposed changes">
        <h2>Waiting for you</h2>
        {record.proposals.map(p => <article key={p.id} className="crm-card">
          <div className="crm-meta">Proposed {p.author_kind && p.author_kind !== "person" ? "by an agent" : ""} on revision {p.base_revision}</div>
          <dl className="crm-fields">{Object.entries(p.fields).map(([name, proposed]) => <div key={name} style={{ display: "contents" }}>
            <dt>{name}</dt><dd>{display(record.fields[name])} → <strong>{display(proposed)}</strong></dd></div>)}</dl>
          {p.narrative != null && <MdxDoc>{p.narrative || "(an empty description)"}</MdxDoc>}
          {p.reason && <p className="crm-note">{p.reason}</p>}
          {p.evidence.length > 0 && <details><summary className="crm-meta">Evidence</summary><pre>{JSON.stringify(p.evidence, null, 2)}</pre></details>}
          {record.actions?.can_review
            ? <div className="crm-actions">
                <button className="crm-button crm-button-primary" disabled={busy}
                  onClick={() => void run(async () => { await crmCall("review", { proposal_id: p.id, accept: true }); await load(); setNotice("Accepted."); })}>Accept</button>
                <button className="crm-button" disabled={busy}
                  onClick={() => void run(async () => { await crmCall("review", { proposal_id: p.id, accept: false }); await load(); setNotice("Rejected."); })}>Reject</button>
              </div>
            : <p className="crm-hint">Someone with review rights on this record accepts or rejects it.</p>}
        </article>)}
      </section>}

      <section className="crm-section" aria-label="History">
        <h2>History</h2>
        {history === null
          ? <button className="crm-button" disabled={busy} onClick={() => void run(async () => {
              setHistory((await crmCall("history", { record_id: record.id })).revisions); })}>Show history</button>
          : <ul className="crm-list">{history.map(h => <li key={h.revision}>
              <span className="crm-meta">#{h.revision} · {h.created_at}</span> {h.reason || ""}</li>)}</ul>}
      </section>
      <p className="crm-hint">To change this record, bind it to a workspace or find another, ask in the chat.</p>
    </>}
  </main>;
}

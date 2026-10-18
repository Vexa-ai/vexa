"use client";
/** THE WORKSPACE PAGE IS ITS README (UI 7aa, 7v; PRD 0.13.4 D17). Below the fixed header, this draws
 *  the render tree the SERVER resolved for this viewer (`POST /api/desktop/page`) as one document:
 *  prose, chips, live lists on the kit's rows with caps and "Show N more", CRM blocks with "only you
 *  see this", action buttons. No frames, no nested scroll, never a raw placeholder or an error —
 *  the server already dropped what this viewer may not see, so nothing here filters or counts.
 *
 *  Nothing runs on render: an action runs only on a click, as the person who clicked, through the
 *  one verb registry (`acts.planPageAction`), confirmed when it reaches beyond their screen. */
import { Fragment, useCallback, useEffect, useState, type ReactNode } from "react";
import { Lock, Zap } from "lucide-react";
import { Markdown } from "../../ui-kit/Markdown";
import { ListRow } from "../../ui-kit/primitives/ListRow";
import { humanizeKey } from "../../ui-kit/primitives/KeyValue";
import { useConfirm } from "../../ui-kit/primitives/Dialog";
import { openRef } from "../../ui-kit/RefChip";
import type { RefAnswer } from "../../data/workspaceApi";
import { OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../platform/events";
import { openCard } from "../desktop/cardsApi";
import { askAgent, planPageAction } from "../desktop/home2/acts";
import { ObjectMenu } from "./ObjectActions";
import "./readme.css";

export type RefOut = { kind: string; access: string; label?: string; open?: Record<string, string | number>; url?: string; request_access?: boolean; proof?: "proven" | "pending" | "stale" };
export type Row = { key: string; title?: string; detail?: string; ref?: string; state?: string; by?: string; at?: number; url?: string;
  restricted?: boolean; act?: { kind: string; session?: string; meeting?: string; card?: string } };
export type ActionNode = { t: "action"; verb: string; label: string; args: Record<string, string>; enabled: boolean; reason?: string; confirm: boolean };
export type CrmRow = { key: string; title: string; type: string; url?: string; fields: { name: string; value: unknown; only_you?: boolean }[] };
export type Node =
  | { t: "md"; text: string }
  | { t: "line"; list?: boolean; parts: ({ t: "text"; text: string } | { t: "ref"; key: string; words?: string } | ActionNode)[] }
  | ActionNode
  | { t: "block"; name: string; title?: string; personal?: boolean; state?: "unavailable" | "pending"; rows?: (Row | CrmRow)[]; limit?: number; more?: number; view?: string };
export type PageTree = { v: number; workspace: string; path: string; title?: string; role?: string; nodes: Node[]; refs: Record<string, RefOut> };

const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));

export async function readPage(workspace: string, path = "README.md", f: typeof fetch = fetch): Promise<PageTree | null> {
  try {
    const r = await f("/api/desktop/page", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workspace, path }) });
    return r.ok ? ((await r.json()) as PageTree) : null;
  } catch { return null; }
}

function Chip({ k, a, words }: { k: string; a?: RefOut; words?: string }) {
  const kind = k.split(":")[0];
  // a claim: the page's own words, then the card behind them (its state for this reader)
  if (words) return <span className="vx-rd-claim" data-claim="" data-proof={a?.proof}>{words}{" "}{a && a.access !== "gone" ? <Chip k={k} a={a} /> : <small className="vx-rd-proof">unproven</small>}</span>;
  if (!a || a.access === "gone") return null;
  if (a.access !== "readable" && a.access !== "grant") {
    // RESTRICTED WITHOUT A NAME (D18): nothing the writer typed, nothing from inside the object
    return <span className="vx-rd-chip" data-kind={kind} data-restricted=""><Lock size={11} aria-hidden /> Restricted</span>;
  }
  const answer = { kind, id: "", url: a.url ?? "", access: a.access, label: a.label ?? null, open: a.open ?? null, request_access: false } as RefAnswer;
  // a claim's proof marker (UI 7aa "cards prove pages"): only when the card seam (S5) answers one
  return <button type="button" className="vx-rd-chip" data-kind={kind} data-ref={k} data-proof={a.proof} onClick={() => openRef(answer)}>
    {a.label || "Untitled"}{a.proof && <small className="vx-rd-proof">{a.proof}</small>}</button>;
}

function Action({ n, workspace, quiet = false }: { n: ActionNode; workspace: string; quiet?: boolean }) {
  const [confirm, dialog] = useConfirm();
  const [busy, setBusy] = useState(false);
  const target = n.args.routine || n.args.name || n.args.prompt || n.args.role || n.args.target || n.args.title || "";
  const go = async () => {
    setBusy(true);
    try {
      if (n.verb === "new-chat") { askAgent(n.args.prompt || "", { draft: true }); return; }
      const plan = await planPageAction(n.verb, target, workspace);
      if (!plan) return;
      if (plan.confirm && !(await confirm(plan.confirm))) return;
      await plan.run();
    } finally { setBusy(false); }
  };
  return <>
    <button type="button" className="vx-rd-act" data-verb={n.verb} disabled={!n.enabled || busy}
      title={n.enabled ? undefined : n.reason} aria-describedby={undefined} onClick={() => void go()}>
      <Zap size={12} aria-hidden />{n.label}</button>
    {!quiet && !n.enabled && n.reason && <small className="vx-rd-reason">{n.reason}</small>}
    {dialog}
  </>;
}

function openRow(r: Row) {
  const a = r.act;
  if (a?.kind === "open-session" && a.session) fire(OPEN_SESSION_EVENT, { session: a.session });
  else if (a?.kind === "open-meeting" && a.meeting) fire(OPEN_MEETING_EVENT, { ref: a.meeting });
  else if (a?.kind === "open-card" && a.card) openCard(a.card);
}

function ago(at?: number): string {
  if (!at) return "";
  const s = Math.max(0, Date.now() / 1000 - at);
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

function ListBlock({ n, workspace }: { n: Extract<Node, { t: "block" }>; workspace: string }) {
  const [all, setAll] = useState(false);
  if (n.state === "unavailable") return <p className="vx-rd-quiet" data-block={n.name}>Not available right now.</p>;
  if (n.state === "pending") return <p className="vx-rd-quiet" data-block={n.name}>Cards will show here.</p>;
  const all_ = (n.rows ?? []) as Row[];
  const rows = all || !n.limit ? all_ : all_.slice(0, n.limit);
  return (
    <section className="vx-rd-block" data-block={n.name} data-personal={n.personal ? "" : undefined}>
      {n.title ? <h2>{n.title}</h2> : null}
      {(n.name === "waiting-on-you" || n.name === "waiting-on-others") && <small className="vx-rd-only">Only you see this</small>}
      <div className="vx-rd-rows">{rows.map((r) => r.restricted
        ? <ListRow key={r.key} leading={<Lock size={13} />} title={<span className="vx-rd-restricted">Restricted</span>}
            meta={[r.by ? `pinned by ${r.by}` : "", ago(r.at)].filter(Boolean).join(" · ") || undefined} />
        : <ListRow key={r.key} title={r.title} twoLine={!!r.detail}
            meta={[r.detail, r.by && n.name === "pinned" ? `pinned by ${r.by}` : "", n.name === "changes-since" || n.name === "pinned" ? ago(r.at) : ""].filter(Boolean).join(" · ") || undefined}
            onSelect={() => openRow(r)} actions={r.ref ? <ObjectMenu target={refTarget(r.ref, r.title)} workspace={workspace} /> : undefined} />)}
      </div>
      {n.more ? (all ? null : <button type="button" className="vx-rd-more" onClick={() => setAll(true)}>Show {n.more} more</button>) : null}
    </section>
  );
}

function refTarget(ref: string, label?: string) {
  const i = ref.indexOf(":");
  return { kind: ref.slice(0, i), id: ref.slice(i + 1), label };
}

function CrmBlock({ n }: { n: Extract<Node, { t: "block" }> }) {
  if (n.state) return <p className="vx-rd-quiet" data-block="crm">Not available right now.</p>;
  return <section className="vx-rd-block vx-rd-crm" data-block="crm">
    {((n.rows ?? []) as CrmRow[]).map((r) => (
      <div key={r.key} className="vx-rd-crm-rec">
        <h3>{r.url ? <a href={r.url}>{r.title}</a> : r.title} <small>{r.type}</small></h3>
        <dl>{r.fields.map((f) => <Fragment key={f.name}><dt>{humanizeKey(f.name)}</dt>
          <dd data-only-you={f.only_you ? "" : undefined}>{String(f.value ?? "")}{f.only_you && <small className="vx-rd-only"> only you see this</small>}</dd></Fragment>)}</dl>
      </div>))}
  </section>;
}

/** `hideTitle`: under the workspace page's fixed header the README's own `# Title` would say the
 *  name twice, so a first-line H1 equal to the page title is not drawn there. */
export function ReadmeDoc({ tree, hideTitle = false }: { tree: PageTree; hideTitle?: boolean }) {
  const ws = tree.workspace;
  const out: ReactNode[] = [];
  tree.nodes.forEach((n, i) => {
    if (n.t === "md") {
      let text = n.text;
      if (hideTitle && i === 0 && tree.title) {
        const m = /^\s*#\s+(.+)\n?/.exec(text);
        if (m && m[1].trim().toLowerCase() === tree.title.trim().toLowerCase()) text = text.slice(m[0].length);
      }
      if (text.trim()) out.push(<Markdown key={i}>{text}</Markdown>);
    }
    else if (n.t === "line") {
      // a disabled action's reason is said once per line, not under every button
      const reasons = [...new Set(n.parts.flatMap((p) => (p.t === "action" && !p.enabled && p.reason ? [p.reason] : [])))];
      out.push(<p key={i} className="vx-rd-line" data-list={n.list ? "" : undefined}>{n.parts.map((p, j) =>
        p.t === "text" ? <Fragment key={j}>{p.text}</Fragment>
          : p.t === "ref" ? <Chip key={j} k={p.key} a={tree.refs[p.key]} words={p.words} />
            : <Action key={j} n={p} workspace={ws} quiet />)}
        {reasons.map((r) => <small key={r} className="vx-rd-reason">{r}</small>)}</p>);
    }
    else if (n.t === "action") out.push(<p key={i} className="vx-rd-line"><Action n={n} workspace={ws} /></p>);
    else if (n.t === "block" && n.name === "crm") out.push(<CrmBlock key={i} n={n} />);
    else if (n.t === "block") out.push(<ListBlock key={i} n={n} workspace={ws} />);
  });
  return <div className="vx-rd" data-readme={ws}>{out}</div>;
}

/** The README under the workspace page's fixed header, refreshed when it might have changed. */
export function ReadmeView({ slug }: { slug: string }) {
  const [tree, setTree] = useState<PageTree | null | undefined>(undefined);
  const load = useCallback(() => { void readPage(slug).then(setTree); }, [slug]);
  useEffect(() => {
    load();
    const t = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, 30_000);
    return () => window.clearInterval(t);
  }, [load]);
  if (tree === undefined) return <div className="vx-rd" aria-busy="true" />;
  if (tree === null) return <p className="vx-rd-quiet">This page isn't available right now.</p>;
  return <ReadmeDoc tree={tree} hideTitle />;
}

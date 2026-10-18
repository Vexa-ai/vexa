"use client";
/** THE RICH PAGE RENDERER (#1783, R3 `home2`; BUILD 2026-10-16 "Rich README"), for any markdown page
 *  in the right panel. The source stays markdown; `richParse` says what is live and this draws it:
 *
 *    · typed references → live chips answered for THIS viewer by `POST /api/desktop/refs`; one the
 *      viewer may not see is never named ("a private room", or nothing);
 *    · badges → the workspace's attention (E1), its people;
 *    · :::columns / :::callout / :::stats (a JSON file in the same workspace);
 *    · [[action:…]] → a button from the fixed allow-list, run as the person who clicks, confirmed
 *      when it reaches beyond their screen (`acts.ts`);
 *    · ```view → a live table or board over pages, cards, meetings or rooms (`ViewBlock`);
 *    · plain tables → search, filter, sort (`LiveTable`).
 *  Everything else goes to the product's own MdxDoc, unchanged. Raw HTML is sanitized first. */
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { Bot, FileText, Hash, Kanban, Lock, MessageSquare, Video, Zap } from "lucide-react";
import { MdxDoc, TableRendererContext, degradeUnknownComponents } from "../../../ui-kit/MdxDoc";
import { readWorkspaceFile } from "../../../data/workspaceApi";
import { OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../../platform/events";
import type { WorkspaceItem } from "../../../platform/desktop";
import { useConfirm } from "../../../ui-kit/primitives/Dialog";
import { Modal } from "../../../ui-kit/Modal";
import { ExternalLink } from "../../../ui-kit/primitives/Links";
import { useDesktopState } from "../desktopApi";
import { Badges, openWorkspace } from "../DesktopTiles";
import { openCard } from "../cardsApi";
import { openDocOnRight, planPageAction } from "./acts";
import { useAttention, workspaceAttention } from "./attentionApi";
import { LiveTable } from "./LiveTable";
import { NeedsList, WorkingChips, useNeedAct } from "./StatusCard";
import { ViewBlock } from "./ViewBlock";
import { WsMark } from "./HomeTarget";
import {
  parseBlocks, parseRefs, sanitizeHtml, splitFrontMatter, splitInline, type ActionVerb, type Badge, type Block,
} from "./richParse";
import "./home2.css";

export type RefOut = {
  kind: string; visible: boolean; name?: string; sub?: string; state?: string; unread?: number; members?: number;
  initials?: string; closed?: boolean; session?: string; meeting?: string; path?: string; slug?: string;
  runs?: { at: number | string; ok: boolean; what: string }[];
};
const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));
const PERSONAL = "personal";

/** The page's references, answered for this viewer in one read (re-read when the page changes). */
export function useRichRefs(keys: string[], workspace: string): Record<string, RefOut> {
  const [m, setM] = useState<Record<string, RefOut>>({});
  const key = keys.join("\n");
  useEffect(() => {
    if (!key) { setM({}); return; }
    let live = true;
    void fetch("/api/desktop/refs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refs: key.split("\n"), workspace }) })
      .then((r) => (r.ok ? r.json() : { refs: {} }))
      .then((d: { refs?: Record<string, RefOut> }) => { if (live) setM(d?.refs ?? {}); })
      .catch(() => { if (live) setM({}); });
    return () => { live = false; };
  }, [key, workspace]);
  return m;
}

function RefChip({ k, r }: { k: string; r?: RefOut }) {
  const [runs, setRuns] = useState(false);
  const kind = k.split(":")[0];
  if (!r) return <span className="vx-rp-chip" data-kind={kind} aria-busy="true">…</span>;
  // ACCESS: a target this viewer may not see is never named — a room says only that it is private
  if (!r.visible) return kind === "room" ? <span className="vx-rp-chip" data-kind="room" data-private=""><Lock size={12} aria-hidden /> a private room</span> : null;
  switch (r.kind) {
    case "room": return <button type="button" className="vx-rp-chip" data-kind="room" data-ref={k} onClick={() => fire(OPEN_SESSION_EVENT, { session: r.session })}>
      {r.closed ? <Lock size={12} aria-hidden /> : <Hash size={12} aria-hidden />}<b>{r.name}</b>{typeof r.members === "number" && <small>{r.members} members</small>}{r.unread ? <i className="vx-rp-unread">{r.unread}</i> : null}</button>;
    case "chat": return <button type="button" className="vx-rp-chip" data-kind="chat" data-ref={k} onClick={() => fire(OPEN_SESSION_EVENT, { session: r.session })}><MessageSquare size={12} aria-hidden /><b>{r.name}</b></button>;
    case "call": return <button type="button" className="vx-rp-chip" data-kind="call" data-ref={k} onClick={() => fire(OPEN_MEETING_EVENT, { ref: String(r.meeting) })}><Video size={12} aria-hidden /><b>{r.name}</b>{r.sub && <small>{r.sub}</small>}</button>;
    case "file": return <button type="button" className="vx-rp-chip" data-kind="file" data-ref={k} onClick={() => r.path && openDocOnRight(r.slug, r.path)}><FileText size={12} aria-hidden /><b>{r.name}</b></button>;
    case "person": return <span className="vx-rp-chip" data-kind="person" data-ref={k}><span className="vx-rp-av" aria-hidden>{r.initials}</span><b>{r.name}</b>{r.sub && <small>{r.sub}</small>}</span>;
    case "card": return <button type="button" className="vx-rp-chip" data-kind="card" data-ref={k} onClick={() => openCard(k.slice(5))}><Kanban size={12} aria-hidden /><b>{r.name}</b>{r.state && <span className="vx-rp-stage" data-stage={r.state}>{r.state}</span>}{r.sub && <small>{r.sub}</small>}</button>;
    case "routine": return <>
      <button type="button" className="vx-rp-chip" data-kind="routine" data-state={r.state} data-ref={k} onClick={() => setRuns(true)} title="What it did"><Bot size={12} aria-hidden /><b>{r.name}</b>{r.sub && <small>{r.sub}</small>}</button>
      {runs && <Modal title={`${r.name} — runs`} onClose={() => setRuns(false)} width={440}>
        <ul className="vx-rp-runs">{(r.runs?.length ? r.runs : [{ at: r.sub ?? "", ok: r.state !== "failed", what: "Last run" }]).map((x, i) => (
          <li key={i} data-ok={x.ok ? "" : undefined}><b>{x.ok ? "✓" : "⚠"} {typeof x.at === "number" ? new Date(x.at * 1000).toLocaleString() : x.at}</b> {x.what}</li>))}</ul></Modal>}</>;
    case "workspace": return <button type="button" className="vx-rp-chip" data-kind="workspace" data-ref={k}
      onClick={() => { const slug = r.slug ?? k.slice(10); void openWorkspace({ slug, id: slug, name: r.name ?? slug } as WorkspaceItem); }}>
      <WsMark slug={r.slug} name={r.name ?? ""} size={14} /><b>{r.name}</b></button>;
    default: return null;
  }
}

function ActionButton({ verb, target, label, slug }: { verb: ActionVerb; target: string; label: string; slug: string }) {
  const [confirm, dialog] = useConfirm();
  const [busy, setBusy] = useState(false);
  // the label is only the button's words: what runs, and what its confirm names, is resolved for
  // this viewer from the target (`acts.planPageAction`)
  const go = async () => {
    setBusy(true);
    try {
      const plan = await planPageAction(verb, target, slug);
      if (!plan) return;
      if (plan.confirm && !(await confirm(plan.confirm))) return;
      await plan.run();
    } finally { setBusy(false); }
  };
  return <><button type="button" className="vx-rp-chip" data-kind="action" data-action={verb} disabled={busy} onClick={() => void go()}><Zap size={12} aria-hidden /><b>{label}</b></button>{dialog}</>;
}

/** the line's own markdown around its chips: links, bold, italics, code */
function inline(s: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*|_([^_]+)_|`([^`]+)`/g;
  let last = 0, k = 0;
  for (const m of s.matchAll(re)) {
    if ((m.index ?? 0) > last) out.push(<Fragment key={k++}>{s.slice(last, m.index)}</Fragment>);
    if (m[1]) out.push(/^(https?:|mailto:)/.test(m[2]) ? <ExternalLink key={k++} href={m[2]}>{m[1]}</ExternalLink> : <Fragment key={k++}>{m[1]}</Fragment>);
    else if (m[3]) out.push(<b key={k++}>{m[3]}</b>);
    else if (m[4] || m[5]) out.push(<em key={k++}>{m[4] ?? m[5]}</em>);
    else if (m[6]) out.push(<code key={k++}>{m[6]}</code>);
    last = (m.index ?? 0) + m[0].length;
  }
  if (last < s.length) out.push(<Fragment key={k++}>{s.slice(last)}</Fragment>);
  return out;
}

function BadgeView({ b, slug, w }: { b: Badge; slug: string; w?: WorkspaceItem }) {
  const { data } = useAttention();
  const a = workspaceAttention(data, slug);
  const [act, dialog] = useNeedAct();
  const live = a.live_call ?? w?.live_call ?? null;
  if (b === "status" || b === "needs-you") {
    return <div className="vx-rp-status" data-badge={b}>
      {a.needs.length > 0
        ? <div className="vx-rp-needs"><h5>Needs you <span className="vx-scard-n"><i aria-hidden />{a.needs.length}</span></h5><NeedsList needs={a.needs} act={(n) => void act(n)} max={3} /></div>
        : <span className="vx-scard-calm">Nothing needs you here</span>}
      {b === "status" && <WorkingChips live={live} a={a} />}
      {dialog}
    </div>;
  }
  if (b === "working-here") return <WorkingChips live={live} a={a} />;
  if (b === "members") return w && w.members.length > 0 ? <span data-badge="members"><Badges people={w.members} /></span> : null;
  // upcoming: the calls within a day, as the read model has them
  const up = a.needs.filter((n) => n.kind === "upcoming");
  return <span data-badge="upcoming">{up.length ? <NeedsList needs={up} act={(n) => void act(n)} max={3} /> : <small className="vx-pb-note">Nothing planned.</small>}{dialog}</span>;
}

function RichLine({ text, refs, slug, w }: { text: string; refs: Record<string, RefOut>; slug: string; w?: WorkspaceItem }) {
  return <div className="vx-rp-line">{splitInline(text).map((p, i) =>
    p.t === "text" ? (p.text.trim() ? <span key={i} className="vx-rp-text">{inline(p.text)}</span> : null)
    : p.t === "ref" ? <RefChip key={i} k={p.key} r={refs[p.key]} />
    : p.t === "badge" ? <BadgeView key={i} b={p.badge} slug={slug} w={w} />
    : <ActionButton key={i} verb={p.verb} target={p.target} label={p.label} slug={slug} />)}</div>;
}

type Tile = { week?: string; at?: string; tiles?: { label: string; value: string; delta?: string; good?: boolean; series?: number[] }[] };
export function Stats({ slug, source }: { slug: string; source: string }) {
  const [d, setD] = useState<Tile | null>(null);
  useEffect(() => {
    if (!source || source.split("/").includes("..")) return;
    void readWorkspaceFile(source, slug === PERSONAL ? undefined : { slug }).then((t) => { try { setD(JSON.parse(t ?? "")); } catch { setD(null); } });
  }, [slug, source]);
  if (!d?.tiles?.length) return null;
  return (
    <div className="vx-rp-stats" aria-label={d.week ? `Figures, ${d.week}` : "Figures"} data-stats={source}>
      {d.tiles.slice(0, 12).map((t) => {
        const series = (t.series ?? []).filter((v) => Number.isFinite(v));
        const min = Math.min(...series), max = Math.max(...series), w = 96, h = 24;
        const pts = series.length > 1 ? series.map((v, i) => `${(i / (series.length - 1)) * w},${h - ((v - min) / (max - min || 1)) * h}`).join(" ") : "";
        return <figure key={t.label} className="vx-rp-tile"><figcaption>{t.label}</figcaption><b>{t.value}</b>
          {t.delta && <span className="vx-rp-delta" data-good={t.good ? "" : undefined}>{t.delta}</span>}
          {pts && <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h} aria-hidden><polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" /></svg>}</figure>;
      })}
      <small className="vx-rp-src">{[d.week, d.at && `written ${d.at}`, source].filter(Boolean).join(" · ")}</small>
    </div>
  );
}

export function RichPage({ slug, path, md }: { slug: string | undefined; path: string; md: string }) {
  const ws = slug || PERSONAL;
  const { body } = useMemo(() => splitFrontMatter(degradeUnknownComponents(sanitizeHtml(md))), [md]);
  const blocks = useMemo(() => parseBlocks(body), [body]);
  const keys = useMemo(() => parseRefs(body).map((r) => r.key), [body]);
  const refs = useRichRefs(keys, ws);
  const { desktop } = useDesktopState(false);
  const w = (desktop?.sections.find((s) => s.id === "workspaces")?.items ?? [])
    .find((i) => i.kind === "workspace" && (i as WorkspaceItem).slug === ws) as WorkspaceItem | undefined;
  const render = (bs: Block[]): ReactNode => bs.map((bk, i) =>
    bk.t === "md" ? <MdxDoc key={i}>{bk.text}</MdxDoc>
    : bk.t === "line" ? <RichLine key={i} text={bk.text} refs={refs} slug={ws} w={w} />
    : bk.t === "columns" ? <div key={i} className="vx-rp-cols" style={{ gridTemplateColumns: `repeat(${Math.min(bk.cols.length, 4)}, minmax(0, 1fr))` }}>{bk.cols.slice(0, 4).map((c, k) => <div key={k} className="vx-rp-col">{render(c)}</div>)}</div>
    : bk.t === "callout" ? <aside key={i} className="vx-rp-callout">{render(bk.inner)}</aside>
    : bk.t === "steps" ? <ol key={i} className="vx-rp-steps">{bk.steps.map((st, k) => <li key={k} data-rp-step="">
        <b className="vx-rp-step-t">{st.title}</b><div className="vx-rp-step-b">{render(st.inner)}</div></li>)}</ol>
    : bk.t === "stats" ? <Stats key={i} slug={ws} source={bk.source} />
    : <ViewBlock key={i} spec={bk.spec} slug={ws} pagePath={path} />);
  return (
    <TableRendererContext.Provider value={LiveTable}>
      <article className="vx-rp" data-rich-page={path}>{render(blocks)}</article>
    </TableRendererContext.Provider>
  );
}

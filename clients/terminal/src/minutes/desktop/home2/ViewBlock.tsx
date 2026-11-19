"use client";
/** A ```view BLOCK IN A PAGE (#1783, R3 `home2`; BUILD 2026-10-16: "```view blocks give live tables
 *  and boards with inline edit through the owner's write path. No code in pages.").
 *
 *    ```view
 *    source: pages | crm.card | meetings | rooms
 *    as: table | board          group: <field>        columns: a, b, c
 *    folder: <path> (pages)     filter: workspace=<slug>, status=open        limit: 50
 *    ```
 *
 *  Every row is something the reader can already open, read with their own key: pages from their
 *  workspace tree, cards on their desk (crm-api), their meetings list, the rooms the attention read
 *  model lists for that workspace. EDITS GO THROUGH THE OWNER'S OWN WRITE PATH and nowhere else:
 *    · a page's field → the page's front matter, saved as a page write (a commit, like any save);
 *    · a card's stage → crm-api's card update with the card's own allowed stages.
 *  Meetings and rooms are read-only here (they open on click). */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../../platform/events";
import type { CardItem } from "../../../platform/desktop";
import { listWorkspaceTree, readWorkspaceFile, writeWorkspaceFile } from "../../../data/workspaceApi";
import { useLiveMeetings } from "../../../surfaces/liveMeetings";
import { toast } from "../../../ui-kit/primitives/Toast";
import { useDesktopState } from "../desktopApi";
import { openCard, updateCard } from "../cardsApi";
import { openDocOnRight } from "./acts";
import { readWorkspaceAttention } from "./attentionApi";
import { setFrontMatterField, splitFrontMatter, type ViewSpec } from "./richParse";
import "./home2.css";

const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));
const PERSONAL = "personal";

/** One row as a view draws it: its fields, how it opens, and how a field is written (if it can be). */
export type ViewRow = {
  id: string; fields: Record<string, string>; open: () => void;
  /** field → the values it may take (a card's stages); absent → free text */
  choices?: Record<string, string[]>;
  write?: (field: string, value: string) => Promise<boolean>;
  editable?: string[];
};

const MAX_PAGES = 50;

async function pageRows(slug: string, folder: string | undefined, limit: number): Promise<{ rows: ViewRow[]; fields: string[] }> {
  const opt = slug === PERSONAL ? undefined : { slug };
  const prefix = (folder ?? "").replace(/^\/+|\/+$/g, "");
  if (prefix.split("/").includes("..")) return { rows: [], fields: [] };
  const files = (await listWorkspaceTree(opt).catch(() => [] as string[]))
    .filter((f) => f.endsWith(".md") && !/(^|\/)(index|README)\.md$/i.test(f) && (!prefix || f.startsWith(`${prefix}/`)))
    .slice(0, Math.min(limit, MAX_PAGES));
  const keys = new Set<string>();
  const rows = await Promise.all(files.map(async (path): Promise<ViewRow> => {
    const src = (await readWorkspaceFile(path, opt).catch(() => null)) ?? "";
    const { meta, body } = splitFrontMatter(src);
    Object.keys(meta).forEach((k) => keys.add(k));
    const title = /^#\s+(.+)$/m.exec(body)?.[1]?.trim() || path.split("/").pop()!.replace(/\.md$/, "");
    const editable = Object.keys(meta);
    return {
      id: path, fields: { title, ...meta }, open: () => openDocOnRight(slug, path), editable,
      write: async (field, value) => {
        // re-read before writing, so a field edit never overwrites a change made since the view loaded
        const now = await readWorkspaceFile(path, opt).catch(() => null);
        if (now == null) { toast("That page is no longer there"); return false; }
        try { await writeWorkspaceFile(path, setFrontMatterField(now, field, value), opt); return true; }
        catch { toast("You can't change this page"); return false; }
      },
    };
  }));
  return { rows, fields: ["title", ...[...keys].filter((k) => k !== "title").slice(0, 6)] };
}

function cardRows(cards: CardItem[], filter: Record<string, string> | undefined): ViewRow[] {
  return cards
    .filter((c) => !filter?.workspace || c.workspaces.includes(filter.workspace))
    .filter((c) => !filter?.stage || c.state === filter.stage)
    .map((c) => ({
      id: c.id, open: () => openCard(c.id),
      fields: { title: c.title, stage: c.state, next: c.next ?? "", due: c.due ?? "", waiting: c.waiting ?? "" },
      ...(c.can.transition ? {
        editable: ["stage"], choices: { stage: [...new Set([c.state, ...c.states])] },
        write: async (_field: string, value: string) => updateCard(c, { transition: value }),
      } : {}),
    }));
}

function useViewRows(spec: ViewSpec, slug: string): { rows: ViewRow[] | null; fields: string[]; reload: () => void } {
  const [state, setState] = useState<{ rows: ViewRow[] | null; fields: string[] }>({ rows: null, fields: [] });
  const [tick, setTick] = useState(0);
  const { desktop } = useDesktopState(false);
  const meetings = useLiveMeetings();
  const cards = useMemo(() => (desktop?.cards_on ? (desktop.sections.find((s) => s.id === "cards")?.items ?? []).filter((i): i is CardItem => i.kind === "card") : []), [desktop]);
  useEffect(() => {
    let live = true;
    const set = (rows: ViewRow[], fields: string[]) => { if (live) setState({ rows: rows.slice(0, spec.limit), fields }); };
    if (spec.source === "pages") void pageRows(slug, spec.folder, spec.limit).then(({ rows, fields }) => set(rows, fields));
    else if (spec.source === "crm.card") set(cardRows(cards, spec.filter), ["title", "stage", "next", "due"]);
    else if (spec.source === "meetings") {
      const ws = spec.filter?.workspace;
      set(meetings.filter((m) => !ws || m.workspace_id === ws).map((m) => ({
        id: m.id, open: () => fire(OPEN_MEETING_EVENT, { ref: String(m.id) }),
        fields: { title: m.title_custom || m.title, when: m.when, status: m.status === "live" ? "live" : (m.live_status ?? m.status) },
      })), ["title", "when", "status"]);
    } else {
      void readWorkspaceAttention(spec.filter?.workspace ?? slug).then((a) => set((a?.rooms ?? []).map((r) => ({
        id: r.session, open: () => fire(OPEN_SESSION_EVENT, { session: r.session }),
        fields: { name: r.name, members: String(r.members), active: r.last_active ? new Date(r.last_active * 1000).toLocaleString() : "" },
      })), ["name", "members", "active"]));
    }
    return () => { live = false; };
    // meetings re-polls on its own; a page view re-reads when asked
  }, [spec, slug, cards, spec.source === "meetings" ? meetings : null, tick]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...state, reload: () => setTick((t) => t + 1) };
}

function Cell({ row, field, onSaved }: { row: ViewRow; field: string; onSaved: () => void }) {
  const [edit, setEdit] = useState<string | null>(null);
  const value = row.fields[field] ?? "";
  const can = !!row.write && !!row.editable?.includes(field);
  const save = async (v: string) => { setEdit(null); if (v !== value && (await row.write!(field, v))) onSaved(); };
  if (!can) return <>{value || <span className="vx-lt-empty">—</span>}</>;
  const choices = row.choices?.[field];
  if (choices) return <select className="vx-lt-q" aria-label={`${field} of ${row.fields.title ?? row.id}`} value={value} onChange={(e) => void save(e.target.value)}>
    {choices.map((o) => <option key={o} value={o}>{o}</option>)}</select>;
  return edit !== null
    ? <input autoFocus className="vx-lt-in" value={edit} aria-label={`Edit ${field}`} onChange={(e) => setEdit(e.target.value)}
        onBlur={() => void save(edit)} onKeyDown={(e) => { if (e.key === "Enter") void save(edit); if (e.key === "Escape") setEdit(null); }} />
    : <button type="button" className="vx-lt-cell" title="Edit" onClick={() => setEdit(value)}>{value || <span className="vx-lt-empty">—</span>}</button>;
}

export function ViewBlock({ spec, slug }: { spec: ViewSpec; slug: string; pagePath?: string }) {
  const { rows, fields: found, reload } = useViewRows(spec, slug);
  const [as, setAs] = useState(spec.as);
  const [q, setQ] = useState("");
  const fields = spec.columns?.length ? spec.columns : found;
  const group = spec.group ?? (spec.source === "crm.card" ? "stage" : fields.find((f) => /^(status|stage|state)$/i.test(f)));
  if (rows === null) return <div className="vx-view vx-view-empty" aria-busy="true">…</div>;
  const needle = q.trim().toLowerCase();
  const shown = rows.filter((r) => !needle || Object.values(r.fields).some((v) => v.toLowerCase().includes(needle)));
  const title = (r: ViewRow): ReactNode => <button type="button" className="vx-lt-cell" onClick={r.open}><b>{r.fields[fields[0]] || r.fields.title || r.fields.name || r.id}</b></button>;
  const groups = group ? [...new Set(rows.map((r) => r.fields[group] ?? ""))] : [];
  return (
    <div className="vx-view vx-lt" data-view={spec.source} data-view-as={as}>
      <div className="vx-lt-bar">
        <input className="vx-lt-q" type="search" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} aria-label={`Search ${spec.source}`} />
        {group && <div className="vx-eff" role="radiogroup" aria-label="View">
          {(["table", "board"] as const).map((v) => <button key={v} type="button" role="radio" aria-checked={as === v} data-on={as === v ? "" : undefined} onClick={() => setAs(v)}>{v === "table" ? "Table" : `Board by ${group}`}</button>)}</div>}
        <span className="vx-lt-count">{shown.length} {shown.length === 1 ? "item" : "items"}</span>
      </div>
      {rows.length === 0 ? <div className="vx-view-empty">Nothing here yet.</div>
      : as === "board" && group ? (
        <div className="vx-lt-board">{groups.map((g) => (
          <section key={g} className="vx-lt-col" data-group={g}><h5>{g || "—"} <small>{shown.filter((r) => (r.fields[group] ?? "") === g).length}</small></h5>
            {shown.filter((r) => (r.fields[group] ?? "") === g).map((r) => (
              <div key={r.id} className="vx-lt-card">{title(r)}
                {fields.slice(1).filter((f) => f !== group && r.fields[f]).map((f) => <small key={f}>{f}: {r.fields[f]}</small>)}
                {r.write && r.editable?.includes(group) && <Cell row={{ ...r, choices: { [group]: r.choices?.[group] ?? groups } }} field={group} onSaved={reload} />}
              </div>))}
          </section>))}</div>
      ) : (
        <div className="vx-prose-table" data-allow-hscroll=""><table>
          <thead><tr>{fields.map((f) => <th key={f}>{f}</th>)}</tr></thead>
          <tbody>{shown.map((r) => <tr key={r.id}>{fields.map((f, c) => <td key={f}>{c === 0 ? title(r) : <Cell row={r} field={f} onSaved={reload} />}</td>)}</tr>)}</tbody>
        </table></div>
      )}
    </div>
  );
}

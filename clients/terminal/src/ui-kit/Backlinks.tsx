"use client";
/** "MENTIONED IN" — the pages that link to this one, at its foot (#1783). Read from the server, which
 *  looks only through workspaces this reader can read. Nothing on a page with no path (a chat reply). */
import { useContext, useEffect, useRef, useState } from "react";
import { DocMetaContext } from "./docRefs";
import { Icon } from "./index";
import { OPEN_PAGE_EVENT } from "../platform/events";

/** OPEN EXACTLY THIS ROW (founder, live: "clicking a Mentioned in chip does nothing"). The server
 *  already named the page — workspace and path — so the click opens THAT, in the right panel, even
 *  in a workspace this chat does not read (the panel says so). It never re-resolves the title the
 *  way a [[link]] is, which is how a page in a workspace outside the chat came to a silent stop. */
export function openMention(r: { slug?: string; path: string }) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(OPEN_PAGE_EVENT, { detail: { workspace: r.slug && r.slug !== "personal" ? r.slug : "", path: r.path } }));
}

type Row = { slug?: string; path: string; title: string; workspace?: string | null };

/** At most this many, then "N more" (founder, live: a "Mentioned in" wall of ~45 chips). */
export const MENTIONS_SHOWN = 5;
const SYSTEM = /(^|\/)(readme|claude|agents|index|policies|onboarding)\.md$|(^|\/)[_.]/i;

/** The mentions worth showing: real pages (never a README, CLAUDE, AGENTS, index or a system
 *  file), one per page, the workspace named only when it is not this page's and never an address. */
export function mentions(rows: Row[], here?: string): (Row & { elsewhere: boolean })[] {
  const seen = new Set<string>();
  const out: (Row & { elsewhere: boolean })[] = [];
  for (const r of rows) {
    if (SYSTEM.test(r.path)) continue;
    const k = `${r.slug ?? ""}:${r.path}`;
    if (seen.has(k)) continue;
    seen.add(k);
    const ws = r.workspace && !r.workspace.includes("@") ? r.workspace : null;
    out.push({ ...r, workspace: ws, elsewhere: !!ws && (r.slug ?? "") !== (here ?? "") });
  }
  return out;
}

export function Backlinks() {
  const meta = useContext(DocMetaContext);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [all, setAll] = useState(false);
  const foot = useRef<HTMLDivElement>(null);
  // READ WHEN THE FOOT OF THE PAGE COMES INTO VIEW: a scan across workspaces is not worth doing for
  // a page nobody scrolls to the end of.
  useEffect(() => {
    setRows(null);
    const el = foot.current;
    if (!meta.path || !el || typeof IntersectionObserver === "undefined") return;
    let on = true;
    const io = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      io.disconnect();
      void import("../data/workspaceApi").then((api) => api.pageBacklinks(meta.path as string, meta.slug))
        .then((r) => { if (on) setRows(r); }, () => { if (on) setRows(null); });
    });
    io.observe(el);
    return () => { on = false; io.disconnect(); };
  }, [meta.path, meta.slug]);
  const shown = mentions(rows ?? [], meta.slug);
  if (shown.length === 0) return <div ref={foot} aria-hidden data-backlinks-sentinel="" />;
  const visible = all ? shown : shown.slice(0, MENTIONS_SHOWN);
  return (
    <section className="vx-backlinks" aria-label="Mentioned in">
      <div className="vx-label">Mentioned in</div>
      <ul>
        {visible.map((r) => (
          <li key={`${r.slug}:${r.path}`}>
            <span role="link" tabIndex={0} className="vx-chip vx-entity vx-inline-chip"
              title={r.workspace && r.slug !== meta.slug ? `${r.title} — in ${r.workspace}` : r.title}
              data-mention={`${r.slug ?? ""}:${r.path}`}
              onClick={() => openMention(r)}
              onKeyDown={(e) => { if (e.key === "Enter") openMention(r); }}>
              <span className="vx-chip-icon" aria-hidden><Icon name="link" size={12} /></span>
              <span className="vx-chip-label">{r.title}</span>
              {r.elsewhere && <span className="vx-chip-meta">{r.workspace}</span>}
            </span>
          </li>
        ))}
      </ul>
      {!all && shown.length > MENTIONS_SHOWN && (
        <button type="button" className="vx-backlinks-more" data-backlinks-more="" onClick={() => setAll(true)}>
          {shown.length - MENTIONS_SHOWN} more</button>
      )}
      {all && shown.length > MENTIONS_SHOWN && (
        <button type="button" className="vx-backlinks-more" data-backlinks-fewer="" onClick={() => setAll(false)}>Show fewer</button>
      )}
    </section>
  );
}

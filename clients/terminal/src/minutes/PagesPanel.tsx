"use client";
/** The room's pages — the context made visible. Header shares the shell's one header row;
 *  chips appear only when there is a choice. */
import { MdxDoc } from "../ui-kit/MdxDoc";
import type { Page } from "./types";
import { header, surface, type as ty } from "./tokens";

export function PagesPanel(p: { pages: Page[]; docPath: string; onOpen: (pg: Page) => void; body: string | null }) {
  return (
    <>
      <div style={{ ...header, gridRow: 1, gridColumn: 3, gap: 6, flexWrap: "nowrap", overflowX: "auto", borderLeft: "1px solid var(--line)" }}>
        <span style={{ ...ty.lens, flex: "none" }}>Pages</span>
        {p.pages.length > 1 && p.pages.map((pg) => (
          <button key={pg.path} onClick={() => p.onOpen(pg)}
            style={{ ...ty.chip, flex: "none", color: p.docPath === pg.path ? "var(--accent)" : "var(--t2)", background: p.docPath === pg.path ? "var(--accentbg)" : surface.raised, border: `1px solid ${p.docPath === pg.path ? "var(--accent)" : "transparent"}`, borderRadius: 6, padding: "3px 10px", cursor: "pointer" }}>
            {pg.label}
          </button>
        ))}
      </div>
      <div style={{ ...ty.body, gridRow: 2, gridColumn: 3, overflowY: "auto", padding: "18px 20px 40px", background: surface.pages, borderLeft: "1px solid var(--line)", minHeight: 0, lineHeight: 1.6, color: "var(--t1)" }}>
        {p.body === null
          ? <div style={{ ...ty.body, color: "var(--t3)", lineHeight: 1.6 }}>No page here yet — it appears when the conversation (or a meeting) writes one.</div>
          : <MdxDoc>{p.body}</MdxDoc>}
      </div>
    </>
  );
}

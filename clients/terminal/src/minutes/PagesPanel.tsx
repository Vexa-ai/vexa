"use client";
/** The room's pages — the context made visible. Header shares the shell's one header row;
 *  chips appear only when there is a choice. */
import { MdxDoc } from "../ui-kit/MdxDoc";
import type { Page } from "./types";
import { header, text } from "./tokens";

export function PagesPanel(p: { pages: Page[]; docPath: string; onOpen: (pg: Page) => void; body: string | null }) {
  return (
    <>
      <div style={{ ...header, gridRow: 1, gridColumn: 3, gap: 6, flexWrap: "nowrap", overflowX: "auto" }}>
        <span style={{ ...text.lens, flex: "none" }}>Pages</span>
        {p.pages.length > 1 && p.pages.map((pg) => (
          <button key={pg.path} onClick={() => p.onOpen(pg)}
            style={{ flex: "none", fontSize: 12, fontWeight: 500, fontFamily: "inherit", color: p.docPath === pg.path ? "var(--accent)" : "var(--t2)", background: "var(--panel2)", border: `1px solid ${p.docPath === pg.path ? "var(--accent)" : "var(--line2)"}`, borderRadius: 6, padding: "3px 10px", cursor: "pointer" }}>
            {pg.label}
          </button>
        ))}
      </div>
      <div style={{ gridRow: 2, gridColumn: 3, overflowY: "auto", padding: "18px 20px 40px", background: "var(--sidebar)", borderLeft: "1px solid var(--line2)", minHeight: 0 }}>
        {p.body === null
          ? <div style={{ fontSize: 13, color: "var(--t3)", lineHeight: 1.6 }}>No page here yet — it appears when the conversation (or a meeting) writes one.</div>
          : <MdxDoc>{p.body}</MdxDoc>}
      </div>
    </>
  );
}

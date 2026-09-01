"use client";
/** The room's pages — the context made visible. Header shares the shell's one header row:
 *  a BREADCRUMB of the open doc (workspace › folders › file) with a View/Edit toggle at the
 *  right (Codex-style, founder ruling 2026-08-22) — docs are EDITABLE in place; Save writes
 *  through the mount-authorized API and commits. Chips appear only when there is a choice. */
import type { CSSProperties } from "react";
import { useEffect, useState } from "react";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { writeWorkspaceFile } from "../surfaces/workspaceApi";
import { MarkdownEditor } from "./MarkdownEditor";
import type { Page } from "./types";
import { header, surface, type as ty } from "./tokens";

/** Breadcrumb separator. Its padding is NBSP *content*, not margin, so it collapses away under
 *  `min-width: 0` instead of holding a permanent sliver open once the crumb has been starved. */
const SEP = " › ";

/** ── The header row yields in a fixed ORDER ──────────────────────────────────────────────────
 *  The chips are what the user ACTS on; the breadcrumb is only orientation. So every item in the
 *  row is `min-width: 0` and shrinkable, and the order in which they give way is set by
 *  flex-shrink factors separated by orders of magnitude — flexbox splits the squeeze by
 *  (shrink × basis), so each rung is effectively exhausted before the next one moves at all:
 *
 *      crumb trail (×10000) ▸ crumb separator (×100) ▸ file name (×1) ▸▸ chips (×1, but the
 *      whole crumb outranks them by CRUMB_SHRINK)
 *
 *  At the 384px default the crumb absorbs the entire overflow on its own and the chips keep their
 *  exact natural widths (measured: they lose < 0.001px, far under Chrome's 1/64px layout unit).
 *  Only below ~364px — where the four chips no longer fit by themselves — do the labels begin to
 *  ellipsize, which keeps every chip present and clickable rather than pushing one off the panel.
 *  The untruncated path stays available on hover via `title`.
 *  NB: these are plain integers on purpose — CSS numbers have no exponent syntax, so `1e4` would
 *  make the whole `flex` declaration invalid and silently drop the ordering. */
const CRUMB_SHRINK = 1000000;
const chipBase: CSSProperties = { flex: "0 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };

export function PagesPanel(p: {
  pages: Page[]; docPath: string; docSlug?: string; onOpen: (pg: Page) => void;
  body: string | null; onSaved?: () => void;
}) {
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // a new doc (or fresh content) always lands in VIEW; edit starts from the live body
  useEffect(() => { setMode("view"); }, [p.docPath, p.docSlug]);

  const crumbs = [p.docSlug ?? "personal", ...p.docPath.split("/").filter(Boolean)];
  const leaf = crumbs[crumbs.length - 1];
  const trail = crumbs.slice(0, -1);
  const fullPath = crumbs.join(SEP);
  const save = async () => {
    setSaving(true);
    try {
      await writeWorkspaceFile(p.docPath, draft, { slug: p.docSlug });
      setMode("view"); p.onSaved?.();
    } catch (e) {
      window.alert(`Could not save: ${e instanceof Error ? e.message : e}`);
    } finally { setSaving(false); }
  };

  return (
    <>
      <div style={{ ...header, gridRow: 1, gridColumn: 3, gap: 6, flexWrap: "nowrap", minWidth: 0, overflowX: "auto", borderLeft: "1px solid var(--line)" }}>
        {/* breadcrumb — the doc's address, and the first thing in this row to give way */}
        <span title={fullPath} style={{ display: "flex", alignItems: "center", flex: `0 ${CRUMB_SHRINK} auto`, minWidth: 0, overflow: "hidden", fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)" }}>
          {trail.length > 0 && (
            <>
              <span style={{ flex: "0 10000 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {trail.map((c, i) => (
                  <span key={i}>
                    {i > 0 && <span style={{ opacity: 0.6 }}>{SEP}</span>}
                    {c}
                  </span>
                ))}
              </span>
              <span style={{ flex: "0 100 auto", minWidth: 0, overflow: "hidden", whiteSpace: "nowrap", opacity: 0.6 }}>{SEP}</span>
            </>
          )}
          <span style={{ flex: "0 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--t1)", fontWeight: 600 }}>{leaf}</span>
        </span>
        <span style={{ flex: "1 0 0%" }} />
        {p.pages.length > 1 && p.pages.map((pg) => (
          <button key={pg.path} onClick={() => p.onOpen(pg)} title={pg.label}
            style={{ ...ty.chip, ...chipBase, color: p.docPath === pg.path ? "var(--accent)" : "var(--t2)", background: p.docPath === pg.path ? "var(--accentbg)" : surface.raised, border: `1px solid ${p.docPath === pg.path ? "var(--accent)" : "transparent"}`, borderRadius: 6, padding: "3px 10px", cursor: "pointer" }}>
            {pg.label}
          </button>
        ))}
        {p.body !== null && (mode === "view"
          ? <button onClick={() => { setDraft(p.body ?? ""); setMode("edit"); }} title="Edit"
              style={{ ...ty.chip, ...chipBase, color: "var(--t2)", background: surface.raised, border: "1px solid transparent", borderRadius: 6, padding: "3px 10px", cursor: "pointer" }}>Edit</button>
          : <>
              <button onClick={() => setMode("view")} title="Cancel"
                style={{ ...ty.chip, ...chipBase, color: "var(--t3)", background: "transparent", border: "none", padding: "3px 6px", cursor: "pointer" }}>Cancel</button>
              <button onClick={() => void save()} disabled={saving} title="Save"
                style={{ ...ty.chip, ...chipBase, color: "#16181d", background: "var(--accent)", border: "none", borderRadius: 6, padding: "3px 12px", cursor: saving ? "default" : "pointer", fontWeight: 600 }}>{saving ? "Saving…" : "Save"}</button>
            </>)}
      </div>
      <div style={{ ...ty.body, gridRow: 2, gridColumn: 3, overflowY: "auto", padding: mode === "edit" ? 0 : "18px 20px 40px", background: surface.pages, borderLeft: "1px solid var(--line)", minHeight: 0, lineHeight: 1.6, color: "var(--t1)", display: mode === "edit" ? "flex" : undefined }}>
        {p.body === null
          ? <div style={{ ...ty.body, color: "var(--t3)", lineHeight: 1.6 }}>No page here yet — it appears when the conversation (or a meeting) writes one.</div>
          : mode === "edit"
            ? <MarkdownEditor value={draft} onChange={setDraft} />
            : <MdxDoc>{p.body}</MdxDoc>}
      </div>
    </>
  );
}

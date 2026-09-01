"use client";
/** The room's pages — the context made visible. Header shares the shell's one header row:
 *  a NAVIGABLE BREADCRUMB of the open doc (workspace › folders › file) with a View/Edit toggle at
 *  the right (Codex-style, founder ruling 2026-08-22) — docs are EDITABLE in place; Save writes
 *  through the mount-authorized API and commits.
 *
 *  TABS, not chips (founder ruling 2026-09-01). Anything opened here — a phase page, an entity
 *  link, a `?view=` deeplink, a file clicked out of a folder listing — ADDS a tab, and tabs close.
 *  The tab strip is not this component's state: it is the CHAT's `artifacts[]`, so the set survives
 *  leaving the chat and the agent's context bundle can name what the human is reading.
 *
 *  The breadcrumb NAVIGATES. Clicking a folder segment lists that folder; clicking a name in the
 *  listing opens it as a tab. Plain names, no icons — the panel is for reading, not for file
 *  management. */
import type { CSSProperties } from "react";
import { useEffect, useState } from "react";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { writeWorkspaceFile } from "../surfaces/workspaceApi";
import { MarkdownEditor } from "./MarkdownEditor";
import type { Page } from "./types";
import { header, surface, type as ty } from "./tokens";

/** Breadcrumb separator. Its padding is NBSP *content*, not margin, so it collapses away under
 *  `min-width: 0` instead of holding a permanent sliver open once the crumb has been starved. */
const SEP = " › ";

/** ── The header row yields in a fixed ORDER ──────────────────────────────────────────────────
 *  The tabs are what the user ACTS on; the breadcrumb is only orientation. So every item in the
 *  row is `min-width: 0` and shrinkable, and the order in which they give way is set by
 *  flex-shrink factors separated by orders of magnitude — flexbox splits the squeeze by
 *  (shrink × basis), so each rung is effectively exhausted before the next one moves at all:
 *
 *      crumb trail (×10000) ▸ crumb separator (×100) ▸ file name (×1) ▸▸ tabs (×1, but the
 *      whole crumb outranks them by CRUMB_SHRINK)
 *
 *  At the 384px default the crumb absorbs the entire overflow on its own and the tabs keep their
 *  exact natural widths (measured: they lose < 0.001px, far under Chrome's 1/64px layout unit).
 *  Only below ~364px — where the tabs no longer fit by themselves — do the labels begin to
 *  ellipsize, which keeps every tab present and clickable rather than pushing one off the panel.
 *  The untruncated path stays available on hover via `title`.
 *  NB: these are plain integers on purpose — CSS numbers have no exponent syntax, so `1e4` would
 *  make the whole `flex` declaration invalid and silently drop the ordering. */
const CRUMB_SHRINK = 1000000;
const chipBase: CSSProperties = { flex: "0 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const crumbBtn: CSSProperties = { background: "transparent", border: "none", padding: 0, margin: 0, font: "inherit", color: "inherit", cursor: "pointer" };

/** A directory listing the breadcrumb navigated to: the folders and files directly under `prefix`. */
export type Listing = { slug?: string; prefix: string; dirs: string[]; files: string[] };

export function PagesPanel(p: {
  pages: Page[]; docPath: string; docSlug?: string; onOpen: (pg: Page) => void;
  onClose?: (pg: Page) => void;
  listing?: Listing | null; onNavigate?: (slug: string | undefined, prefix: string) => void;
  body: string | null; onSaved?: () => void;
}) {
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // a new doc (or fresh content) always lands in VIEW; edit starts from the live body
  useEffect(() => { setMode("view"); }, [p.docPath, p.docSlug]);

  const listing = p.listing ?? null;
  // While a listing is up the breadcrumb addresses the FOLDER, not the last document read.
  const crumbs = listing
    ? [listing.slug ?? "personal", ...listing.prefix.split("/").filter(Boolean)]
    : [p.docSlug ?? "personal", ...p.docPath.split("/").filter(Boolean)];
  const leaf = crumbs[crumbs.length - 1];
  const trail = crumbs.slice(0, -1);
  const fullPath = crumbs.join(SEP);
  const slug = listing ? listing.slug : p.docSlug;
  // segment i (0 = the workspace root) addresses the folder made of segments 1..i
  const nav = (i: number) => p.onNavigate?.(slug, crumbs.slice(1, i + 1).join("/"));
  const save = async () => {
    setSaving(true);
    try {
      await writeWorkspaceFile(p.docPath, draft, { slug: p.docSlug });
      setMode("view"); p.onSaved?.();
    } catch (e) {
      window.alert(`Could not save: ${e instanceof Error ? e.message : e}`);
    } finally { setSaving(false); }
  };

  const tabOn = (pg: Page) => !listing && p.docPath === pg.path && (pg.slug ?? undefined) === (p.docSlug ?? undefined);

  return (
    <>
      <div style={{ ...header, gridRow: 1, gridColumn: 3, gap: 6, flexWrap: "nowrap", minWidth: 0, overflowX: "auto", borderLeft: "1px solid var(--line)" }}>
        {/* breadcrumb — the doc's address, the first thing in this row to give way, and a way back
            up the tree: every segment is a button that lists that folder. */}
        <span title={fullPath} style={{ display: "flex", alignItems: "center", flex: `0 ${CRUMB_SHRINK} auto`, minWidth: 0, overflow: "hidden", fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)" }}>
          {trail.length > 0 && (
            <>
              <span style={{ flex: "0 10000 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {trail.map((c, i) => (
                  <span key={i}>
                    {i > 0 && <span style={{ opacity: 0.6 }}>{SEP}</span>}
                    <button style={crumbBtn} title={i === 0 ? `List ${c}` : `List ${crumbs.slice(1, i + 1).join("/")}`}
                      onClick={() => nav(i)}
                      onMouseEnter={(e) => { e.currentTarget.style.color = "var(--accent)"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.color = "inherit"; }}>{c}</button>
                  </span>
                ))}
              </span>
              <span style={{ flex: "0 100 auto", minWidth: 0, overflow: "hidden", whiteSpace: "nowrap", opacity: 0.6 }}>{SEP}</span>
            </>
          )}
          {listing
            ? <button style={{ ...crumbBtn, flex: "0 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--t1)", fontWeight: 600, cursor: "default" }}>{leaf}</button>
            : <span style={{ flex: "0 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--t1)", fontWeight: 600 }}>{leaf}</span>}
        </span>
        <span style={{ flex: "1 0 0%" }} />
        {p.pages.map((pg) => {
          const on = tabOn(pg);
          return (
            <span key={`${pg.slug ?? ""}|${pg.path}`} style={{ ...chipBase, display: "inline-flex", alignItems: "center", background: on ? "var(--accentbg)" : surface.raised, border: `1px solid ${on ? "var(--accent)" : "transparent"}`, borderRadius: 6 }}>
              <button onClick={() => p.onOpen(pg)} title={pg.slug ? `${pg.slug} › ${pg.path}` : pg.path}
                style={{ ...ty.chip, ...chipBase, color: on ? "var(--accent)" : "var(--t2)", background: "transparent", border: "none", padding: p.onClose && p.pages.length > 1 ? "3px 3px 3px 10px" : "3px 10px", cursor: "pointer" }}>
                {pg.label}
              </button>
              {p.onClose && p.pages.length > 1 && (
                <button aria-label={`Close ${pg.label}`} title="Close tab" onClick={(e) => { e.stopPropagation(); p.onClose?.(pg); }}
                  style={{ background: "transparent", border: "none", color: on ? "var(--accent)" : "var(--t3)", cursor: "pointer", fontSize: 12, lineHeight: 1, padding: "0 6px 0 2px", fontFamily: "var(--sans)" }}>×</button>
              )}
            </span>
          );
        })}
        {!listing && p.body !== null && (mode === "view"
          ? <button onClick={() => { setDraft(p.body ?? ""); setMode("edit"); }} title="Edit"
              style={{ ...ty.chip, ...chipBase, color: "var(--t2)", background: surface.raised, border: "1px solid transparent", borderRadius: 6, padding: "3px 10px", cursor: "pointer" }}>Edit</button>
          : <>
              <button onClick={() => setMode("view")} title="Cancel"
                style={{ ...ty.chip, ...chipBase, color: "var(--t3)", background: "transparent", border: "none", padding: "3px 6px", cursor: "pointer" }}>Cancel</button>
              <button onClick={() => void save()} disabled={saving} title="Save"
                style={{ ...ty.chip, ...chipBase, color: "#16181d", background: "var(--accent)", border: "none", borderRadius: 6, padding: "3px 12px", cursor: saving ? "default" : "pointer", fontWeight: 600 }}>{saving ? "Saving…" : "Save"}</button>
            </>)}
      </div>
      <div style={{ ...ty.body, gridRow: 2, gridColumn: 3, overflowY: "auto", padding: mode === "edit" && !listing ? 0 : "18px 20px 40px", background: surface.pages, borderLeft: "1px solid var(--line)", minHeight: 0, lineHeight: 1.6, color: "var(--t1)", display: mode === "edit" && !listing ? "flex" : undefined }}>
        {listing
          ? <FolderListing listing={listing} onNavigate={p.onNavigate} onOpen={p.onOpen} />
          : p.body === null
            ? <div style={{ ...ty.body, color: "var(--t3)", lineHeight: 1.6 }}>No page here yet — it appears when the conversation (or a meeting) writes one.</div>
            : mode === "edit"
              ? <MarkdownEditor value={draft} onChange={setDraft} />
              : <MdxDoc>{p.body}</MdxDoc>}
      </div>
    </>
  );
}

const entryS: CSSProperties = {
  display: "block", width: "100%", textAlign: "left", background: "transparent", border: "none",
  padding: "4px 6px", borderRadius: 6, cursor: "pointer", fontFamily: "var(--mono)", fontSize: 12.5,
};

/** A folder, as a list of names. Directories first, then files; clicking a directory goes deeper,
 *  clicking a file opens it as a tab. Deliberately plain — this is orientation, not a file manager. */
function FolderListing(p: { listing: Listing; onNavigate?: (slug: string | undefined, prefix: string) => void; onOpen: (pg: Page) => void }) {
  const { slug, prefix, dirs, files } = p.listing;
  const at = (name: string) => (prefix ? `${prefix}/${name}` : name);
  if (!dirs.length && !files.length) {
    return <div style={{ ...ty.body, color: "var(--t3)" }}>Nothing in this folder.</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
      {dirs.map((d) => (
        <button key={"d/" + d} style={{ ...entryS, color: "var(--t2)" }} onClick={() => p.onNavigate?.(slug, at(d))}
          onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>{d}/</button>
      ))}
      {files.map((f) => (
        <button key={"f/" + f} style={{ ...entryS, color: "var(--t1)" }}
          onClick={() => p.onOpen({ path: at(f), slug, label: f.replace(/\.md$/i, "") })}
          onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>{f}</button>
      ))}
    </div>
  );
}

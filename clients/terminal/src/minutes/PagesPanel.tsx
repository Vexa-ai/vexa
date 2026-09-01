"use client";
/** The room's pages — the context made visible.
 *
 *  TABS, not chips (founder ruling 2026-09-01). Anything opened here — a phase page, an entity
 *  link, a `?view=` deeplink, a file clicked out of a folder listing — ADDS a tab, and tabs close.
 *  The tab strip is not this component's state: it is the CHAT's `artifacts[]`, so the set survives
 *  leaving the chat and the agent's context bundle can name what the human is reading. The header
 *  row (the shell's shared 46px band) is theirs, with the View/Edit toggle at the right
 *  (Codex-style, founder ruling 2026-08-22) — docs are EDITABLE in place; Save writes through the
 *  mount-authorized API and commits.
 *
 *  The BREADCRUMB moved out of that row, onto its own strip at the top of the body.
 *  3875079b6 taught the header to sacrifice the crumb before the chips, and that was right while
 *  the crumb was decoration: you starve what nobody clicks. Making it NAVIGABLE inverted the
 *  premise — a squeezed crumb is now a broken control, and with close buttons on every tab the two
 *  were fighting over 46px hard enough that the tab strip painted over the crumb and swallowed its
 *  clicks (caught by the harness, not by the eye). Two rows, no contest, and the crumb gets the
 *  full width it needs to be a path you can walk: clicking a segment lists that folder, clicking a
 *  name in the listing opens it as a tab. Plain names, no icons — this panel is for reading, not
 *  file management.
 */
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

/** Tabs do NOT shrink. Five of them in a 384px panel had ellipsized to "T..×  M..×  P..×" — every
 *  tab present, every one unreadable, which is a worse failure than not seeing them all. So each
 *  keeps a legible width and the STRIP scrolls, the way a browser's does; the full path stays on
 *  hover via `title`. Nav arrows and the edit control sit outside that scroller and never move. */
const chipBase: CSSProperties = { flex: "0 0 auto", maxWidth: 150, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const crumbBtn: CSSProperties = { background: "transparent", border: "none", padding: 0, margin: 0, font: "inherit", color: "inherit", cursor: "pointer" };
const navBtn = (on: boolean): CSSProperties => ({
  flex: "none", width: 22, height: 24, display: "flex", alignItems: "center", justifyContent: "center",
  background: "transparent", border: "none", borderRadius: 6, fontFamily: "var(--sans)", fontSize: 17,
  lineHeight: 1, color: on ? "var(--t2)" : "var(--line2)", cursor: on ? "pointer" : "default", padding: 0,
});

/** A directory listing the breadcrumb navigated to: the folders and files directly under `prefix`. */
export type Listing = { slug?: string; prefix: string; dirs: string[]; files: string[] };

export function PagesPanel(p: {
  pages: Page[]; docPath: string; docSlug?: string; onOpen: (pg: Page) => void;
  onClose?: (pg: Page) => void;
  listing?: Listing | null; onNavigate?: (slug: string | undefined, prefix: string) => void;
  canBack?: boolean; canForward?: boolean; onBack?: () => void; onForward?: () => void;
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
        {/* where you have BEEN, at the panel's left edge — the reading order of a document surface
            starts here (Obsidian, and the old terminal, both put them exactly there). */}
        <button data-nav="back" aria-label="Back" title="Back (⌘/Ctrl + [)" disabled={!p.canBack} onClick={p.onBack} style={navBtn(!!p.canBack)}>‹</button>
        <button data-nav="forward" aria-label="Forward" title="Forward (⌘/Ctrl + ])" disabled={!p.canForward} onClick={p.onForward} style={navBtn(!!p.canForward)}>›</button>
        <div style={{ flex: "1 1 0%", minWidth: 0, display: "flex", alignItems: "center", gap: 6, overflowX: "auto", overflowY: "hidden", paddingLeft: 2 }}>
        {p.pages.map((pg) => {
          const on = tabOn(pg);
          return (
            <span key={`${pg.slug ?? ""}|${pg.path}`} style={{ ...chipBase, display: "inline-flex", alignItems: "center", background: on ? "var(--accentbg)" : surface.raised, border: `1px solid ${on ? "var(--accent)" : "transparent"}`, borderRadius: 6 }}>
              <button data-tab onClick={() => p.onOpen(pg)} title={pg.slug ? `${pg.slug} › ${pg.path}` : pg.path}
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
        </div>
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
      <div style={{ gridRow: 2, gridColumn: 3, display: "flex", flexDirection: "column", minHeight: 0, background: surface.pages, borderLeft: "1px solid var(--line)" }}>
        {/* the breadcrumb — the doc's address, and a path you can walk back up */}
        <div title={fullPath} style={{ flex: "none", display: "flex", alignItems: "center", gap: 0, padding: "7px 20px 6px", borderBottom: "1px solid var(--line)", fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)", overflowX: "auto", whiteSpace: "nowrap" }}>
          {trail.map((c, i) => (
            <span key={i} style={{ flex: "none" }}>
              {i > 0 && <span style={{ opacity: 0.6 }}>{SEP}</span>}
              <button style={crumbBtn} title={i === 0 ? `List ${c}` : `List ${crumbs.slice(1, i + 1).join("/")}`}
                onClick={() => nav(i)}
                onMouseEnter={(e) => { e.currentTarget.style.color = "var(--accent)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = "inherit"; }}>{c}</button>
            </span>
          ))}
          {trail.length > 0 && <span style={{ flex: "none", opacity: 0.6 }}>{SEP}</span>}
          <span style={{ flex: "none", color: "var(--t1)", fontWeight: 600 }}>{leaf}</span>
        </div>
        <div style={{ ...ty.body, flex: 1, overflowY: "auto", padding: mode === "edit" && !listing ? 0 : "18px 20px 40px", minHeight: 0, lineHeight: 1.6, color: "var(--t1)", display: mode === "edit" && !listing ? "flex" : undefined }}>
          {listing
            ? <FolderListing listing={listing} onNavigate={p.onNavigate} onOpen={p.onOpen} />
            : p.body === null
              ? <div style={{ ...ty.body, color: "var(--t3)", lineHeight: 1.6 }}>No page here yet — it appears when the conversation (or a meeting) writes one.</div>
              : mode === "edit"
                ? <MarkdownEditor value={draft} onChange={setDraft} />
                : <MdxDoc>{p.body}</MdxDoc>}
        </div>
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
        <button key={"d/" + d} data-entry="dir" style={{ ...entryS, color: "var(--t2)" }} onClick={() => p.onNavigate?.(slug, at(d))}
          onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>{d}/</button>
      ))}
      {files.map((f) => (
        <button key={"f/" + f} data-entry="file" style={{ ...entryS, color: "var(--t1)" }}
          onClick={() => p.onOpen({ path: at(f), slug, label: f.replace(/\.md$/i, "") })}
          onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>{f}</button>
      ))}
    </div>
  );
}

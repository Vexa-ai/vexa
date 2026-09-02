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
import { Icon } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { writeWorkspaceFile } from "../surfaces/workspaceApi";
import { MarkdownEditor } from "./MarkdownEditor";
import type { Page } from "./types";
import { CollapseButton } from "./Collapse";
import { Navigator } from "./Navigator";
import { isMachineryEntry } from "./machinery";
import { loadNavOpen, saveNavOpen } from "./navigatorApi";
import { registry } from "../contributions";
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

/** The doc header's utility group — one size, one shape, `on` for a control that is currently
 *  holding the view (the source toggle) or has just fired (copy). */
const iconBtn = (on: boolean): CSSProperties => ({
  flex: "none", width: 26, height: 24, display: "flex", alignItems: "center", justifyContent: "center",
  background: on ? surface.raisedHi : "transparent", border: "none", borderRadius: 6,
  color: on ? "var(--t1)" : "var(--t3)", cursor: "pointer", padding: 0, transition: "color .12s, background .12s",
});
const litIcon = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.color = "var(--t1)"; e.currentTarget.style.background = surface.raised; };
const dimIcon = (on: boolean) => (e: { currentTarget: HTMLElement }) => {
  e.currentTarget.style.color = on ? "var(--t1)" : "var(--t3)";
  e.currentTarget.style.background = on ? surface.raisedHi : "transparent";
};

/** A directory listing the breadcrumb navigated to: the folders and files directly under `prefix`. */
export type Listing = { slug?: string; prefix: string; dirs: string[]; files: string[] };

export function PagesPanel(p: {
  pages: Page[]; docPath: string; docSlug?: string; onOpen: (pg: Page) => void;
  onClose?: (pg: Page) => void;
  listing?: Listing | null; onNavigate?: (slug: string | undefined, prefix: string) => void;
  canBack?: boolean; canForward?: boolean; onBack?: () => void; onForward?: () => void;
  docKind?: "doc" | "meeting";
  body: string | null; onSaved?: () => void;
  onCollapse?: () => void;
}) {
  // THE TRANSCRIPT TAB IS NOT A DOCUMENT. It renders the meeting canvas the workbench registers —
  // the same component a meetings-list click opens, which fetches by row id and streams live
  // segments while the bot is in the room. Reached through the tab REGISTRY rather than by
  // importing the surface, so this panel keeps the dependency direction the registry exists for:
  // surfaces register, shells render what is registered.
  const canvas = p.docKind === "meeting" && !p.listing;
  const MeetingCanvas = canvas ? registry.tabComponent("meeting") : undefined;
  // THE NAVIGATOR'S DOOR (PRD decision 27.4). Default hidden, remembered per browser — the boolean
  // lives here rather than in the shell because the rail is part of this panel, and the panel is
  // already the thing that knows whether it is folded away at all.
  const [navOpen, setNavOpen] = useState<boolean>(() => loadNavOpen());
  const showNav = (v: boolean) => { setNavOpen(v); saveNavOpen(v); };
  const [mode, setMode] = useState<"view" | "edit">("view");
  // RAW is a lens on the view, not a third mode: `</>` shows the markdown the renderer was given,
  // which is the question it answers ("what is actually in the file?"). Keeping it orthogonal to
  // `mode` means Edit can be reached from either lens and returns to the one you were in.
  const [raw, setRaw] = useState(false);
  const [copied, setCopied] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // A failed save reports INLINE, beside the button that failed. It used to be a window.alert(),
  // which blocks the main thread — so React could not repaint and the button sat frozen on
  // "Saving…" behind the dialog, reading as a hang on top of the failure.
  const [saveError, setSaveError] = useState<string | null>(null);
  // a new doc (or fresh content) always lands in VIEW, rendered — the lens is a per-document choice
  useEffect(() => { setMode("view"); setRaw(false); setSaveError(null); }, [p.docPath, p.docSlug]);
  useEffect(() => { if (!copied) return; const t = setTimeout(() => setCopied(false), 1400); return () => clearTimeout(t); }, [copied]);

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
  // The doc header's two halves (founder reference: `2026-09-01-vexa-prd.md  drafts`) — the file's
  // own name, and where it lives. Read off the DOCUMENT, never off the crumb, so a folder listing
  // open in front of it cannot rename the document sitting behind it.
  const docName = p.docPath.split("/").pop() || p.docPath;
  const docWhere = [p.docSlug ?? "personal", ...p.docPath.split("/").slice(0, -1)].join(" / ");
  const doc = !canvas && !listing;   // a document is in front — the only state the header describes
  const save = async () => {
    setSaving(true); setSaveError(null);
    try {
      await writeWorkspaceFile(p.docPath, draft, { slug: p.docSlug });
      setMode("view"); p.onSaved?.();
    } catch (e) {
      // Stay in edit mode with the draft intact — the text is the one thing that must not be lost.
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally { setSaving(false); }
  };

  const tabOn = (pg: Page) => !listing && p.docPath === pg.path && (pg.slug ?? undefined) === (p.docSlug ?? undefined);

  return (
    <>
      <div style={{ ...header, gridRow: 1, gridColumn: 3, gap: 6, flexWrap: "nowrap", minWidth: 0, overflowX: "auto", borderLeft: "1px solid var(--line)" }}>
        {/* where you have BEEN, at the panel's left edge — the reading order of a document surface
            starts here (Obsidian, and the old terminal, both put them exactly there). */}
        {/* the navigator's toggle — the panel's leftmost control, because the rail it opens is the
            panel's leftmost column. One button, no other chrome (decision 27.4). */}
        <button data-nav-toggle aria-pressed={navOpen} aria-label={navOpen ? "Hide the file navigator" : "Show the file navigator"}
          title={navOpen ? "Hide the file navigator" : "Show the file navigator (Esc closes, / filters)"}
          onClick={() => showNav(!navOpen)} style={iconBtn(navOpen)}
          onMouseEnter={litIcon} onMouseLeave={dimIcon(navOpen)}>
          <Icon name="folder" size={13} />
        </button>
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
        {/* Edit/Cancel/Save used to sit here, competing with the tabs for the same 46px. They are
            DOCUMENT controls, so they moved down into the doc header's utility group with the rest
            of them — which leaves this row to do the one job it is named for. */}
        {/* outside the tab scroller (`flex: none`), so it never scrolls out of reach */}
        {p.onCollapse && <CollapseButton side="right" onClick={p.onCollapse} />}
      </div>
      <div style={{ gridRow: 2, gridColumn: 3, display: "flex", minHeight: 0, minWidth: 0, background: surface.pages, borderLeft: "1px solid var(--line)" }}>
        {/* The rail sits INSIDE the panel, under the shared header band — beside the open file, the
            way the founder's reference has it. `onOpenTab` is this panel's own open route, so an
            explicit open-in-tab lands on the chat record exactly like a link click does; a plain
            click never comes through here at all (decision 28). */}
        {navOpen && <Navigator onOpenTab={p.onOpen} onClose={() => showNav(false)} />}
        <div style={{ flex: "1 1 0%", display: "flex", flexDirection: "column", minHeight: 0, minWidth: 0 }}>
        {/* WHAT is in front, and what can be done to it — the document's own header row.
            Filename prominent, location subdued beside it, every utility grouped hard right
            (founder reference, the desktop app's doc panel). Three rows now stack above the body
            and each answers a different question: the tabs say what is OPEN, this says what is IN
            FRONT, the crumb below says where it LIVES and walks you back up.
            A canvas is exempt — it names its own meeting in its own header, and there is no file
            here to read as source, copy or edit, so the whole row (not just the group) stands down. */}
        {doc && <div style={{ flex: "none", display: "flex", alignItems: "baseline", gap: 8, padding: "9px 20px 8px", borderBottom: "1px solid var(--line)", minWidth: 0 }}>
          <span data-doc-name title={docName}
            style={{ ...ty.title, fontSize: 13.5, color: "var(--t1)", flex: "0 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{docName}</span>
          <span data-doc-where title={docWhere}
            style={{ ...ty.meta, flex: "0 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{docWhere}</span>
          <span style={{ flex: "1 1 0%", minWidth: 8 }} />
          {p.body !== null && (mode === "view"
            ? <>
                <button data-doc-act="raw" aria-pressed={raw} onClick={() => setRaw((v) => !v)}
                  title={raw ? "Show the rendered document" : "Show the markdown source"} aria-label="Toggle markdown source"
                  style={iconBtn(raw)} onMouseEnter={litIcon} onMouseLeave={dimIcon(raw)}>
                  <Icon name="code" size={14} />
                </button>
                <button data-doc-act="copy" onClick={() => { void copyText(p.body ?? ""); setCopied(true); }}
                  title={copied ? "Copied" : "Copy contents"} aria-label="Copy contents"
                  style={iconBtn(copied)} onMouseEnter={litIcon} onMouseLeave={dimIcon(copied)}>
                  <Icon name={copied ? "check" : "copy"} size={14} />
                </button>
                <button data-doc-act="edit" onClick={() => { setDraft(p.body ?? ""); setSaveError(null); setMode("edit"); }}
                  title="Edit" aria-label="Edit"
                  style={iconBtn(false)} onMouseEnter={litIcon} onMouseLeave={dimIcon(false)}>
                  <Icon name="edit" size={14} />
                </button>
              </>
            : <>
                {saveError && <span data-doc-act="save-error" role="alert" title={saveError}
                  style={{ ...ty.meta, flex: "0 1 auto", minWidth: 0, color: "var(--danger)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>Could not save: {saveError}</span>}
                <button data-doc-act="cancel" onClick={() => { setSaveError(null); setMode("view"); }} title="Cancel"
                  style={{ ...ty.chip, flex: "none", color: "var(--t3)", background: "transparent", border: "none", padding: "3px 6px", cursor: "pointer" }}>Cancel</button>
                <button data-doc-act="save" onClick={() => void save()} disabled={saving} title="Save"
                  style={{ ...ty.chip, flex: "none", color: "var(--on-accent)", background: "var(--accent)", border: "none", borderRadius: 6, padding: "3px 12px", cursor: saving ? "default" : "pointer", fontWeight: 600 }}>{saving ? "Saving…" : "Save"}</button>
              </>)}
        </div>}
        {/* the breadcrumb — the doc's address, and a path you can walk back up. A canvas has no
            address: its `path` is a row id, and the canvas names the meeting in its own header. */}
        {!canvas && <div title={fullPath} style={{ flex: "none", display: "flex", alignItems: "center", gap: 0, padding: "7px 20px 6px", borderBottom: "1px solid var(--line)", fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)", overflowX: "auto", whiteSpace: "nowrap" }}>
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
        </div>}
        <div style={{ ...ty.body, flex: 1, overflowY: canvas ? "hidden" : "auto", padding: canvas || (mode === "edit" && !listing) ? 0 : "18px 20px 40px", minHeight: 0, lineHeight: 1.6, color: "var(--t1)", display: canvas || (mode === "edit" && !listing) ? "flex" : undefined }}>
          {canvas
            // the canvas owns its own scrolling, header and padding — it is a whole surface, not a body
            ? (MeetingCanvas
                ? <MeetingCanvas id={`meeting:${p.docPath}`} params={{ meetingId: p.docPath }} active />
                : <div style={{ ...ty.body, color: "var(--t3)", padding: "18px 20px" }}>The meeting surface is not registered in this build.</div>)
            : listing
              ? <FolderListing listing={listing} onNavigate={p.onNavigate} onOpen={p.onOpen} />
              : p.body === null
                ? <div style={{ ...ty.body, color: "var(--t3)", lineHeight: 1.6 }}>No page here yet — it appears when the conversation (or a meeting) writes one.</div>
                : mode === "edit"
                  ? <MarkdownEditor value={draft} onChange={setDraft} />
                  : raw
                    // the bytes, not a second editor — selectable and copyable, never writable,
                    // so `</>` stays a lens and Edit stays the one way to change a file
                    ? <pre data-doc-raw style={{ margin: 0, fontFamily: "var(--mono)", fontSize: 12.5, lineHeight: 1.65, color: "var(--t1)", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{p.body}</pre>
                    : <MdxDoc>{p.body}</MdxDoc>}
        </div>
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
  const { slug, prefix } = p.listing;
  const at = (name: string) => (prefix ? `${prefix}/${name}` : name);
  // HUMAN FILES ONLY (decision 27.2) — and from the same list `./machinery` gives the navigator,
  // so a folder walked here and the same folder expanded there can never disagree.
  const dirs = p.listing.dirs.filter((d) => !isMachineryEntry(prefix, d));
  const files = p.listing.files.filter((f) => !isMachineryEntry(prefix, f));
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

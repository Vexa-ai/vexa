"use client";
/** The room's pages — the context made visible.
 *
 *  OBSIDIAN'S RULE (founder ruling 2026-09-06: *"no need to create tabs, unless there is a pinned
 *  tab. Use obsidian rule for that and tab icon is on tab"*). Anything opened here — a phase page,
 *  an entity link, a `?view=` deeplink, a file clicked out of a folder listing — REPLACES what is
 *  in front and stands in the strip's ONE preview slot; the next page you open replaces it in turn.
 *  A page becomes a TAB only when somebody asked for it: the reader pinned it, from the pin ON the
 *  tab, or a scaffold declared it. Before this, opening four documents left four tabs, which is
 *  what the founder walked into.
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
import { useEffect, useRef, useState } from "react";
import { Icon } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import { DocMetaContext } from "../ui-kit/docRefs";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { transcriptSlotMeeting } from "../ui-kit/transcriptSlot";
import { writeWorkspaceFile } from "../surfaces/workspaceApi";
import { MarkdownEditor } from "./MarkdownEditor";
import type { Page } from "./types";
import { CollapseButton } from "./Collapse";
import { Navigator } from "./Navigator";
import { isMachineryEntry } from "./machinery";
import { loadNavOpen, saveNavOpen } from "./navigatorApi";
import { CreatePageButton, ExtendPageButton, SelectionExtend, useIntentLanding } from "./ExtendAction";
import { registry } from "../contributions";
import { ReportPageButton } from "../surfaces/ReportThis";
import { header, surface, type as ty } from "./tokens";
import { WorkspaceReadmePanel } from "./WorkspaceReadmePanel";
import { isWorkspaceReadme } from "./workspaceReadme";

/** Breadcrumb separator. Its padding is NBSP *content*, not margin, so it collapses away under
 *  `min-width: 0` instead of holding a permanent sliver open once the crumb has been starved. */
const SEP = " › ";

/** Tabs do NOT shrink. Five of them in a 384px panel had ellipsized to "T..×  M..×  P..×" — every
 *  tab present, every one unreadable, which is a worse failure than not seeing them all. So each
 *  keeps a legible width and the STRIP scrolls, the way a browser's does; the full path stays on
 *  hover via `title`. Nav arrows and the edit control sit outside that scroller and never move. */
const chipBase: CSSProperties = { flex: "0 0 auto", maxWidth: 150, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
/** A tab's BOX and the label inside it. The box grew when the pin moved onto the tab: a label
 *  allotted the full 150px would push its own pin out of the box that clips it. */
const tabBox: CSSProperties = { ...chipBase, maxWidth: 176 };
const tabLabel: CSSProperties = { ...chipBase, maxWidth: 104 };
/** The controls a tab carries — the pin, and `×`. Small, and lit only on the tab in front. */
const tabBtn = (on: boolean): CSSProperties => ({
  flex: "none", display: "flex", alignItems: "center", justifyContent: "center", width: 18, height: 20,
  background: "transparent", border: "none", borderRadius: 4, padding: 0, cursor: "pointer",
  color: on ? "var(--accent)" : "var(--t3)", fontFamily: "var(--sans)", fontSize: 12, lineHeight: 1,
});
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

/** WHAT A WORKSPACE SEGMENT IS CALLED WHEN A PERSON READS IT. A desk's slug is its owner's user
 *  number and the private system workspace lives under `.system/<number>`; both are addresses, not
 *  names, and the crumb was printing them raw — "173 › identity.md" — so the reader met a number
 *  where their own desk should be. Founder, twice in one walk: "what is 171?", "173 is unhelpful".
 *  A bare number here is only ever the reader's own desk (nobody else's desk is mounted in this
 *  panel), so it reads "personal"; `.system` reads "private" and the number under it collapses. */
function crumbLabel(seg: string, i: number, all: string[]): string | null {
  if (seg === ".system") return "private";
  if (/^\d+$/.test(seg)) return i === 1 && all[0] === ".system" ? null : "personal";
  return seg;
}

/** A directory listing the breadcrumb navigated to: the folders and files directly under `prefix`. */
export type Listing = { slug?: string; prefix: string; dirs: string[]; files: string[] };

export function PagesPanel(p: {
  pages: Page[]; docPath: string; docSlug?: string; onOpen: (pg: Page) => void;
  onClose?: (pg: Page) => void;
  /** THE PIN, PER TAB (founder ruling 2026-09-06: *"tab icon is on tab"*). Keep that page as a tab,
   *  or give it back to the preview slot. Absent = no pin control rendered. */
  onTogglePin?: (pg: Page) => void;
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
  // The rendered document's own box — the scope a text selection must be inside to be THIS page's
  // (see SelectionExtend), and the positioning context the floating action sits in.
  const docBox = useRef<HTMLDivElement | null>(null);
  // One listener for the panel: when an Extend/Create turn commits, its page becomes the view.
  useIntentLanding();
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [copied, setCopied] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // A failed save reports INLINE, beside the button that failed. It used to be a window.alert(),
  // which blocks the main thread — so React could not repaint and the button sat frozen on
  // "Saving…" behind the dialog, reading as a hang on top of the failure.
  const [saveError, setSaveError] = useState<string | null>(null);
  // a new doc (or fresh content) always lands in VIEW, rendered
  useEffect(() => { setMode("view"); setSaveError(null); }, [p.docPath, p.docSlug]);
  useEffect(() => { if (!copied) return; const t = setTimeout(() => setCopied(false), 1400); return () => clearTimeout(t); }, [copied]);

  const listing = p.listing ?? null;
  // While a listing is up the breadcrumb addresses the FOLDER, not the last document read.
  const crumbs = listing
    ? [listing.slug ?? "personal", ...listing.prefix.split("/").filter(Boolean)]
    : [p.docSlug ?? "personal", ...p.docPath.split("/").filter(Boolean)];
  const shown = crumbs.map((c, i) => ({ i, c, label: crumbLabel(c, i, crumbs) })).filter((x) => x.label !== null);
  const leaf = shown[shown.length - 1]?.label ?? crumbs[crumbs.length - 1];
  const trail = shown.slice(0, -1);
  const fullPath = shown.map((x) => x.label).join(SEP);
  const slug = listing ? listing.slug : p.docSlug;
  // segment i (0 = the workspace root) addresses the folder made of segments 1..i
  const nav = (i: number) => p.onNavigate?.(slug, crumbs.slice(1, i + 1).join("/"));
  // The doc header's two halves (founder reference: `2026-09-01-vexa-prd.md  drafts`) — the file's
  // own name, and where it lives. Read off the DOCUMENT, never off the crumb, so a folder listing
  // open in front of it cannot rename the document sitting behind it.
  const docName = p.docPath.split("/").pop() || p.docPath;

  const doc = !canvas && !listing;   // a document is in front — the only state the header describes
  // WHICH MEETING THIS PAGE IS, according to the page (Vexa-ai/vexa#1598). A meeting doc declares its
  // transcript widget in its own source, and that declaration is what makes Extend here the
  // meeting-doc act — read since the page's cursor, write into its regions. Read off the BODY rather
  // than off the shell's open chat, for the same reason the acts read the resolved slot rather than
  // the tab label (F63): a fact about the document beats a display string about the session.
  const docMeeting = transcriptSlotMeeting(p.body ?? "") || undefined;
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
          // KEPT = a tab. Everything else in the strip is the one preview slot, and it renders in
          // italic for the same reason Obsidian does: it is going to be replaced by whatever you
          // open next, and that is worth knowing before you navigate away from it.
          const kept = !!pg.pinned || !!pg.desk;
          return (
            <span key={`${pg.slug ?? ""}|${pg.path}`} style={{ ...tabBox, display: "inline-flex", alignItems: "center", background: on ? "var(--accentbg)" : surface.raised, border: `1px solid ${on ? "var(--accent)" : "transparent"}`, borderRadius: 6 }}>
              <button data-tab data-kept={kept ? "" : undefined} onClick={() => p.onOpen(pg)} title={pg.slug ? `${pg.slug} › ${pg.path}` : pg.path}
                style={{ ...ty.chip, ...tabLabel, fontStyle: kept ? undefined : "italic", color: on ? "var(--accent)" : "var(--t2)", background: "transparent", border: "none", padding: "3px 3px 3px 10px", cursor: "pointer" }}>
                {/^\d+$/.test(pg.label) ? "personal" : pg.label}
              </button>
              {/* THE PIN, ON THE TAB. The chat's home carries none: it is a product default rather
                  than something the reader asked for, so there is no decision here to offer. Nor
                  does a page the MEETING owns (Vexa-ai/vexa#1600) — and there the control would be
                  worse than pointless, because unpinning a tab that is not in front drops it, which
                  is the close this tab must not have. */}
              {p.onTogglePin && !pg.desk && !pg.permanent && (
                <button data-tab-pin aria-pressed={kept} aria-label={kept ? `Unpin ${pg.label}` : `Keep ${pg.label} as a tab`}
                  title={kept ? "Unpin — this goes back to being the page you are reading" : "Keep this as a tab"}
                  onClick={(e) => { e.stopPropagation(); p.onTogglePin?.(pg); }}
                  style={{ ...tabBtn(on), opacity: kept ? 1 : 0.55 }}>
                  <Icon name="pin" size={11} />
                </button>
              )}
              {/* `×` — EXCEPT ON THE MEETING'S OWN PAGES (Vexa-ai/vexa#1600). Founder, on the
                  "Open transcript" chip that used to stand beside the composer: *"just keep a tab
                  that can't be closed instead"*. A chip is a way back from a mistake the product
                  did not have to allow; a tab with no `×` is the mistake not being available. So in
                  a meeting chat the transcript, and the meeting's page when it has one, carry no
                  close control at all — and an ordinary pinned tab keeps its own, because a pin is
                  the reader saying "keep this" and what the reader kept the reader may drop.
                  NOR ON THE CHAT'S HOME. `forgetHistory` has always refused the desk entry — it is
                  a product default, not something the reader put there — so a `×` on it was a dead
                  control advertising a close the product does not have: the defect #1600 removed
                  for the meeting's tabs, one tab to the left. It stands down on `!desk` exactly as
                  the pin above does. */}
              {p.onClose && !pg.permanent && !pg.desk && p.pages.length > 1 && (
                <button data-tab-close aria-label={`Close ${pg.label}`} title="Close tab" onClick={(e) => { e.stopPropagation(); p.onClose?.(pg); }}
                  style={{ ...tabBtn(on), width: 16, marginRight: 3 }}>×</button>
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
          {/* ONE PATH LINE (PRD decision 28, founder: *"duplicated paths"*). This span repeated the
              folder trail that the breadcrumb directly below already shows, and navigates. The name
              belongs here; the path belongs there. */}
          <span style={{ flex: "1 1 0%", minWidth: 8 }} />
          {/* WHAT IS LEFT IN THIS GROUP, and why each of the three that went, went. The PIN moved
              onto the tab (*"tab icon is on tab"*) — it is a fact about a tab, not about the header.
              The `</>` RAW LENS is gone outright (*"remove raw markdown button"*): it answered a
              question a reader of a document does not ask, and Edit already shows the source to
              anyone who does. EXTEND moved under the content, where it is a labelled control rather
              than the sixth spark-shaped glyph in a row. */}
          {p.body !== null && (mode === "view"
            ? <>
                <button data-doc-act="copy" onClick={() => { void copyText(p.body ?? ""); setCopied(true); }}
                  title={copied ? "Copied" : "Copy contents"} aria-label="Copy contents"
                  style={iconBtn(copied)} onMouseEnter={litIcon} onMouseLeave={dimIcon(copied)}>
                  <Icon name={copied ? "check" : "copy"} size={14} />
                </button>
                {/* PRD decision 33 §2 — this page is wrong, or is not the page I asked for. The
                    RESOLVED view slot, never the tab label or the crumb (F63): those are display
                    strings, and a report built from one names a file nobody opened. */}
                <ReportPageButton workspace={p.docSlug} path={p.docPath} />
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
        {/* ONE NAME (founder ruling 2026-09-06: *"no need to duplicate doc name"*). The header
            directly above already says which file is in front; this row ended in the same string,
            so the screen said it twice. What is left is the FOLDER TRAIL — the question this row
            answers, and the only part of it you can click. A folder LISTING has no header above it,
            so there the last segment is the folder you are standing in and it stays. */}
        {!canvas && (!doc || trail.length > 0) && <div title={fullPath} style={{ flex: "none", display: "flex", alignItems: "center", gap: 0, padding: "7px 20px 6px", borderBottom: "1px solid var(--line)", fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)", overflowX: "auto", whiteSpace: "nowrap" }}>
          {trail.map(({ i, label: c }) => (
            <span key={i} style={{ flex: "none" }}>
              {i > 0 && <span style={{ opacity: 0.6 }}>{SEP}</span>}
              <button style={crumbBtn} title={i === 0 ? `List ${c}` : `List ${crumbs.slice(1, i + 1).join("/")}`}
                onClick={() => nav(i)}
                onMouseEnter={(e) => { e.currentTarget.style.color = "var(--accent)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = "inherit"; }}>{c}</button>
            </span>
          ))}
          {!doc && <>
            {trail.length > 0 && <span style={{ flex: "none", opacity: 0.6 }}>{SEP}</span>}
            <span style={{ flex: "none", color: "var(--t1)", fontWeight: 600 }}>{leaf}</span>
          </>}
        </div>}
        <div ref={docBox} data-doc-body style={{ ...ty.body, position: "relative", flex: 1, overflowY: canvas ? "hidden" : "auto", padding: canvas || (mode === "edit" && !listing) ? 0 : "18px 20px 40px", minHeight: 0, lineHeight: 1.6, color: "var(--t1)", display: canvas || (mode === "edit" && !listing) ? "flex" : undefined }}>
          {canvas
            // the canvas owns its own scrolling, header and padding — it is a whole surface, not a body
            ? (MeetingCanvas
                ? <MeetingCanvas id={`meeting:${p.docPath}`} params={{ meetingId: p.docPath }} active />
                : <div style={{ ...ty.body, color: "var(--t3)", padding: "18px 20px" }}>The meeting surface is not registered in this build.</div>)
            : listing
              ? <FolderListing listing={listing} onNavigate={p.onNavigate} onOpen={p.onOpen} />
              : p.body === null
                ? <div style={{ ...ty.body, color: "var(--t3)", lineHeight: 1.6 }}>
                    <div>No page here yet — it appears when the conversation (or a meeting) writes one.</div>
                    {/* …or you ask for it now (decision 32.4). Same resolved slot as the header. */}
                    <CreatePageButton workspace={p.docSlug} path={p.docPath} />
                  </div>
                : mode === "edit"
                  ? <MarkdownEditor value={draft} onChange={setDraft} slug={p.docSlug} />
                  /* A WORKSPACE README IS ITS FRONT PAGE (Vexa-ai/vexa#1623). Founder, 2026-09-06,
                     looking at a customer workspace's README in this very slot: *"if it's a
                     workspace readme we want to have data — shared with whom, controls like github
                     sync, git history lookup"*. It stands between the slug line above and the prose
                     below, and INSIDE this scroller rather than above it, so a long panel scrolls
                     with the document instead of squeezing it out of the panel.
                     Only on the root README, only while reading it: `drafts/README.md` is a page
                     about drafts and an editor is editing a file, not consulting a workspace. */
                  /* WHERE THIS DOCUMENT LIVES. `DocMetaContext` is how every link renderer learns
                     the base a relative reference resolves against and the workspace to read it
                     from — the workspace surface has provided it since it existed, and this panel
                     never did, so a doc opened HERE resolved its own neighbours against the
                     reader's primary workspace. It went unnoticed while the only consumers were
                     links (which mostly still land, via the search order); an IMAGE has no search
                     order — the picture is either in this workspace or it is missing (#1612). */
                  : <DocMetaContext.Provider value={{ path: p.docPath, slug: p.docSlug }}>
                      {isWorkspaceReadme(p.docPath) && <WorkspaceReadmePanel slug={p.docSlug} path={p.docPath} />}
                      <MdxDoc>{p.body}</MdxDoc>
                    </DocMetaContext.Provider>}
          {/* EXTEND, UNDER THE CONTENT (decision 32.1, as ruled 2026-09-06). Only while READING a
              document that exists: an empty page offers Create instead, and a canvas has no page to
              extend at all. */}
          {doc && p.body !== null && mode === "view" && (
            <ExtendPageButton workspace={p.docSlug} path={p.docPath} meeting={docMeeting} />
          )}
          {/* PRD decision 32.1's second trigger. Only while READING — an editor's selection is
              being edited, not asked about. */}
          {doc && p.body !== null && mode === "view" && (
            <SelectionExtend containerRef={docBox} workspace={p.docSlug} path={p.docPath} body={p.body} meeting={docMeeting} />
          )}
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
  // so a folder walked here and the same folder expanded there can never disagree. The listing's
  // own `slug` goes with the question, because the answer differs by workspace (#1626).
  const dirs = p.listing.dirs.filter((d) => !isMachineryEntry(prefix, d, slug));
  const files = p.listing.files.filter((f) => !isMachineryEntry(prefix, f, slug));
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

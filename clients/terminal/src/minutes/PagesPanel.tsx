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
import { Identity } from "../ui-kit/Identity";
import { editableNote } from "./noteMarkers";
import { AttachMeetingDialog } from "./AttachMeetingDialog";
import { isMeetingNotePath } from "./roomView";
import { plainPage, sourcesBlock } from "../ui-kit/plainPage";
import { ImageViewer, isImageFile } from "./ImageViewer";
import { PageHead, PagePath, pageTitle as pageHeadTitle } from "./PageHead";
import { Menu } from "../ui-kit/primitives/Menu";
import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { MEETING_DELETED_EVENT } from "./meetingDeletion";
import { Breadcrumb, Button, EmptyState, Icon, IconButton, OverflowStrip } from "../ui-kit";
import { ChevronLeft, ChevronRight, FileText, Folder, MoreHorizontal, Pin, X } from "lucide-react";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import { pageLabel } from "./callsList";
import { copyText } from "../ui-kit/ContextMenu";
import { DocMetaContext } from "../ui-kit/docRefs";
import { MdxDoc, TableRendererContext } from "../ui-kit/MdxDoc";
import { transcriptSlotMeeting } from "../ui-kit/transcriptSlot";
import { writeWorkspaceFile } from "../data/workspaceApi";
import { MarkdownEditor } from "./MarkdownEditor";
import type { Page } from "./types";
import { CollapseButton } from "./Collapse";
import { Navigator } from "./Navigator";
import { isMachineryEntry } from "./machinery";
import { loadNavOpen, saveNavOpen } from "./navigatorApi";
import { CreatePageButton, ExtendPageButton, SelectionExtend, useIntentLanding } from "./ExtendAction";
import { registry } from "../contributions";
import { header, surface, type as ty } from "./tokens";
import { WorkspaceReadmePanel } from "./WorkspaceReadmePanel";
import { splitLeadingH1 } from "./workspaceFrontPage";
import { isWorkspaceReadme } from "./workspaceReadme";
import { WORKSPACE_FRONT_PAGE } from "./deepLink";
import { useActiveCall } from "./callState";
import { showPage } from "./callShow";
import { ROOM_LINES_REFRESH, stopRoomShow, useRoomShow } from "./roomShow";
import { toast } from "../ui-kit/primitives/Toast";
import { useFeature } from "../platform/features";
import { RichPage } from "./desktop/home2/RichPage";
import { LiveTable } from "./desktop/home2/LiveTable";
import { hasRichSyntax } from "./desktop/home2/richParse";
import { WorkspacePage, isWorkspacePagePath } from "./desktop/home2/WorkspacePage";
import { artifactTime, artifactVerb, type ChatArtifact } from "./chatArtifacts";
import { useDesktopItems, type DesktopTarget } from "./desktop/useDesktopItems";
import { ContextMenu } from "../ui-kit/ContextMenu";
import { DeletePageDialog, PageRemovedToast, pageTitle, useCanDelete, workspaceLabel, type PageTarget } from "./pageDelete";
import { useCardItems } from "./desktop/useCardItems";

/** Breadcrumb separator. Its padding is NBSP *content*, not margin, so it collapses away under
 *  `min-width: 0` instead of holding a permanent sliver open once the crumb has been starved. */
const SEP = " › ";

/** Tabs do NOT shrink below a legible width (72–176px, `.vx-doctab`): five of them in a 384px panel
 *  had ellipsized to "T..×  M..×  P..×" — every tab present, every one unreadable. The STRIP
 *  scrolls instead, and its overflow menu lists every tab; the full path stays on hover. */
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

/** A tab's identity in the strip, and the name it shows (a desk's slug is its owner's number). */
const tabKey = (pg: Page) => `${pg.slug ?? ""}|${pg.path}`;
const tabName = (pg: Page) => (/^\d+$/.test(pg.label) ? "personal" : pg.label);

/** A directory listing the breadcrumb navigated to: the folders and files directly under `prefix`. */
export type Listing = { slug?: string; prefix: string; dirs: string[]; files: string[];
  /** the workspace's human name, when the opener knew it — the crumb's root reads it, never the slug */
  name?: string };

/** A call's tab is named by the meeting as it is NOW, so a rename reaches tabs already open. Its own
 *  component, so only a strip that holds a call subscribes to the meetings store. */
function CallTabLabel({ pg }: { pg: Page }) {
  const meetings = useLiveMeetings();
  if (pg.live) return <span data-tab-live="" className="vx-tab-live"><span aria-hidden className="vx-tab-live-dot" />Live · {pageLabel(pg, meetings)}</span>;
  return <>{pageLabel(pg, meetings)}</>;
}

export function PagesPanel(p: {
  pages: Page[]; docPath: string; docSlug?: string; onOpen: (pg: Page) => void;
  /** the room in front (rooms): its pages can be shown to every member */
  roomSession?: string;
  /** the open chat's read set — a page outside it is marked "not read by this chat" */
  chatReads?: string[];
  onClose?: (pg: Page) => void;
  /** THE PIN, PER TAB (founder ruling 2026-09-06: *"tab icon is on tab"*). Keep that page as a tab,
   *  or give it back to the preview slot. Absent = no pin control rendered. */
  onTogglePin?: (pg: Page) => void;
  listing?: Listing | null; onNavigate?: (slug: string | undefined, prefix: string) => void;
  canBack?: boolean; canForward?: boolean; onBack?: () => void; onForward?: () => void;
  docKind?: "doc" | "meeting";
  body: string | null; onSaved?: () => void;
  /** WHY THERE IS NO DOCUMENT — one sentence, in the panel, where the page a link named would have
   *  been (Vexa-ai/vexa#1643). It replaces the body rather than sitting above it, because the whole
   *  defect it answers is a panel that showed the reader the USUAL page and said nothing: somebody
   *  who followed a link and met the desk's README cannot tell a spent link from a broken product,
   *  and the second reading is the one they take. Set only by a refusal; cleared by opening
   *  anything at all. */
  notice?: string | null;
  onCollapse?: () => void;
  /** THIS CHAT'S ARTIFACTS (founder, 2026-10-15: *"right sidebar artefacts belong to the agent on
   *  the left side of it"*): what the panel shows when nothing is open — never a workspace tree. */
  artifacts?: ChatArtifact[];
  onArtifact?: (a: ChatArtifact) => void;
  /** The fold control's name where folding really closes a sheet ("Back to the conversation"). */
  collapseLabel?: string;
}) {
  // THE TRANSCRIPT TAB IS NOT A DOCUMENT. It renders the meeting canvas the workbench registers —
  // the same component a meetings-list click opens, which fetches by row id and streams live
  // segments while the bot is in the room. Reached through the tab REGISTRY rather than by
  // importing the surface, so this panel keeps the dependency direction the registry exists for:
  // surfaces register, shells render what is registered.
  // A NOTICE OUTRANKS THE CANVAS: the sentence is about the link, not about whatever tab the
  // chat happened to open with, and the canvas is a whole surface that would render over it.
  // A held notice belongs to a link that would not open; a meeting in front outranks it.
  const canvas = p.docKind === "meeting" && !p.listing;
  const MeetingCanvas = canvas ? registry.tabComponent("meeting") : undefined;
  // THE NAVIGATOR'S DOOR (PRD decision 27.4). Default hidden, remembered per browser — the boolean
  // lives here rather than in the shell because the rail is part of this panel, and the panel is
  // already the thing that knows whether it is folded away at all.
  const [navOpen, setNavOpen] = useState<boolean>(() => loadNavOpen());
  // ONE PANEL AT A TIME (founder, UI lab 2026-10-14): with a meeting in front the files fold away —
  // the meeting is a sidebar, not a column beside a tree. The folder button then swaps the panel to
  // the files and back, for this meeting only; a document keeps the person's remembered choice.
  const [navOverMeeting, setNavOverMeeting] = useState(false);
  useEffect(() => { setNavOverMeeting(false); }, [p.docPath, p.docKind]);
  const navShown = canvas ? navOverMeeting : navOpen;
  const [navReveal, setNavReveal] = useState<{ slug?: string; dir: string; at: number } | null>(null);
  const showNav = (v: boolean) => { if (canvas) { setNavOverMeeting(v); return; } setNavOpen(v); saveNavOpen(v); };
  // The rendered document's own box — the scope a text selection must be inside to be THIS page's
  // (see SelectionExtend), and the positioning context the floating action sits in.
  const docBox = useRef<HTMLDivElement | null>(null);
  // One listener for the panel: when an Extend/Create turn commits, its page becomes the view.
  useIntentLanding();
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [copied, setCopied] = useState(false);
  const [details, setDetails] = useState(false);
  useEffect(() => { setDetails(false); }, [p.docPath, p.docSlug]);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // A failed save reports INLINE, beside the button that failed. It used to be a window.alert(),
  // which blocks the main thread — so React could not repaint and the button sat frozen on
  // "Saving…" behind the dialog, reading as a hang on top of the failure.
  const [saveError, setSaveError] = useState<string | null>(null);
  // DELETE (founder 2026-10-11: *"we probably need a delete for a note"*) — offered where the
  // reader may edit; not on a meeting's page (it has its own delete) nor a workspace README.
  const [deleting, setDeleting] = useState<PageTarget | null>(null);
  const mayDelete = useCanDelete(p.docSlug);
  // Add to desktop / Remove from desktop for the page in front (desktop ADR D1, 15e)
  const desktopItems = useDesktopItems();
  // SHOW TO EVERYONE (founder 2026-10-15): in a call, a page of a shared workspace can be shown to
  // everyone in it; the server refuses a page someone cannot read, and the dock offers to share first.
  const { current: inCall } = useActiveCall();
  const showOn = useFeature("call_show");
  // home2 (R3): pages using the rich grammar are drawn live, and plain tables can be searched and sorted
  const richOn = useFeature("home2");
  const canShow = !!inCall && showOn && !!p.docSlug && p.docSlug !== "personal" && p.docSlug !== "_global";
  // SHOW TO ROOM (JTBD-1, rooms): in a room with no in-app call, the page opens in every member's panel
  const roomShow = useRoomShow(p.roomSession);
  const canShowRoom = !inCall && !!p.roomSession && !!p.docSlug && p.docSlug !== "personal" && p.docSlug !== "_global";
  const showingThis = roomShow.mine && roomShow.showing?.workspace === p.docSlug && roomShow.showing?.path === p.docPath;
  const cardItems = useCardItems();
  // a new doc (or fresh content) always lands in VIEW, rendered
  useEffect(() => { setMode("view"); setSaveError(null); }, [p.docPath, p.docSlug]);
  useEffect(() => { if (!copied) return; const t = setTimeout(() => setCopied(false), 1400); return () => clearTimeout(t); }, [copied]);

  const listing = p.listing ?? null;
  // While a listing is up the breadcrumb addresses the FOLDER, not the last document read.
  const crumbs = listing
    ? [listing.slug ?? "personal", ...listing.prefix.split("/").filter(Boolean)]
    : [p.docSlug ?? "personal", ...p.docPath.split("/").filter(Boolean)];
  const shown = crumbs.map((c, i) => ({ i, c, label: i === 0 && listing?.name ? listing.name : crumbLabel(c, i, crumbs) })).filter((x) => x.label !== null);
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

  // NOTHING OPEN: the panel is this chat's list of artifacts, with every file one click deeper.
  const home = !canvas && !listing && !p.docPath;
  const doc = !canvas && !listing && !home;   // a document is in front — the only state the header describes
  // WHICH MEETING THIS PAGE IS, according to the page (Vexa-ai/vexa#1598). A meeting doc declares its
  // transcript widget in its own source, and that declaration is what makes Extend here the
  // meeting-doc act — read since the page's cursor, write into its regions. Read off the BODY rather
  // than off the shell's open chat, for the same reason the acts read the resolved slot rather than
  // the tab label (F63): a fact about the document beats a display string about the session.
  const docMeeting = transcriptSlotMeeting(p.body ?? "") || undefined;
  // A MEETING DELETED FROM ITS OWN PAGE (#1783) closes that page: there is nothing left to read.
  const closeRef = useRef<() => void>(() => {});
  closeRef.current = () => { const pg = p.pages.find(tabOn); if (pg) p.onClose?.(pg); };
  useEffect(() => {
    if (!docMeeting) return;
    const on = (e: Event) => { const d = (e as CustomEvent<{ id?: string; native?: string }>).detail; if (d && (d.id === docMeeting || (d.native && d.native === docMeeting))) closeRef.current(); };
    window.addEventListener(MEETING_DELETED_EVENT, on);
    return () => window.removeEventListener(MEETING_DELETED_EVENT, on);
  }, [docMeeting]);
  const restoreRef = useRef<(t: string) => string>((t) => t);
  const [attaching, setAttaching] = useState(false);
  const save = async () => {
    setSaving(true); setSaveError(null);
    try {
      await writeWorkspaceFile(p.docPath, restoreRef.current(draft), { slug: p.docSlug });
      setMode("view"); p.onSaved?.();
    } catch (e) {
      // Stay in edit mode with the draft intact — the text is the one thing that must not be lost.
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally { setSaving(false); }
  };

  const tabOn = (pg: Page) => !listing && p.docPath === pg.path && (pg.slug ?? undefined) === (p.docSlug ?? undefined);

  return (
    <>
      {/* The header band never scrolls sideways (guidelines §3.4): the tab strip inside it does,
          with a fade and an overflow menu, and everything else here is a fixed-size control. */}
      <div className="vx-pane vx-pages-head" data-pane="pages-header" style={{ gridRow: 1, gridColumn: 3 }}>
        {/* where you have BEEN, at the panel's left edge — the reading order of a document surface
            starts here (Obsidian, and the old terminal, both put them exactly there). */}
        {/* the navigator's toggle — the panel's leftmost control, because the rail it opens is the
            panel's leftmost column. One button, no other chrome (decision 27.4). */}
        <IconButton data-nav-toggle pressed={navShown} label={navShown ? "Hide the file navigator" : "Show the file navigator"}
          onClick={() => showNav(!navShown)}>
          <Icon name="folder" size={14} />
        </IconButton>
        <IconButton data-nav="back" label="Back" disabled={!p.canBack} onClick={p.onBack}><ChevronLeft size={16} strokeWidth={1.75} /></IconButton>
        <IconButton data-nav="forward" label="Forward" disabled={!p.canForward} onClick={p.onForward}><ChevronRight size={16} strokeWidth={1.75} /></IconButton>
        {/* NO HIDDEN TABS (guidelines §3.3): the strip fades at an edge with tabs beyond it, and an
            overflow control lists every tab whenever one is not fully in view. */}
        <OverflowStrip label="tabs" activeKey={(() => { const a = p.pages.find(tabOn); return a ? tabKey(a) : undefined; })()}
          items={p.pages.map((pg) => ({ key: tabKey(pg), label: tabName(pg), onSelect: () => p.onOpen(pg) }))}>
        {p.pages.map((pg) => {
          const on = tabOn(pg);
          // KEPT = a tab. Everything else in the strip is the one preview slot, and it renders in
          // italic for the same reason Obsidian does: it is going to be replaced by whatever you
          // open next, and that is worth knowing before you navigate away from it.
          const kept = !!pg.pinned || !!pg.desk;
          return (
            // THE DOCUMENT TAB (guidelines §4.6, `document` skin): the tab in front joins the page
            // below it (surface-1, primary text) — no accent border; accent is not "the open tab".
            <span key={tabKey(pg)} data-strip-key={tabKey(pg)} className="vx-doctab" data-active={on ? "" : undefined} data-preview={kept ? undefined : ""}>
              <button data-tab data-kept={kept ? "" : undefined} onClick={() => p.onOpen(pg)} title={pg.slug ? `${pg.slug} › ${pg.path}` : pg.path}
                className="vx-doctab-label" aria-current={on || undefined}>
                {pg.kind === "meeting" ? <CallTabLabel pg={pg} /> : tabName(pg)}
              </button>
              {/* THE PIN, ON THE TAB. The chat's home carries none: it is a product default rather
                  than something the reader asked for, so there is no decision here to offer. Nor
                  does a page the MEETING owns (Vexa-ai/vexa#1600) — and there the control would be
                  worse than pointless, because unpinning a tab that is not in front drops it, which
                  is the close this tab must not have. */}
              {p.onTogglePin && !pg.desk && !pg.permanent && (
                <button data-tab-pin aria-pressed={kept} aria-label={kept ? `Unpin ${pg.label}` : `Keep ${pg.label} as a tab`}
                  title={kept ? "Unpin — this goes back to being the page you are reading" : "Keep this as a tab"}
                  onClick={(e) => { e.stopPropagation(); p.onTogglePin?.(pg); }} className="vx-doctab-act">
                  <Pin size={12} strokeWidth={1.75} fill={kept ? "currentColor" : "none"} aria-hidden />
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
              {p.onClose && !pg.permanent && !pg.desk && p.pages.length > 0 && (
                <button data-tab-close aria-label={`Close ${pg.label}`} title="Close tab" onClick={(e) => { e.stopPropagation(); p.onClose?.(pg); }}
                  className="vx-doctab-act"><X size={12} strokeWidth={1.75} aria-hidden /></button>
              )}
            </span>
          );
        })}
        </OverflowStrip>
        {/* Edit/Cancel/Save used to sit here, competing with the tabs for the same 46px. They are
            DOCUMENT controls, so they moved down into the doc header's utility group with the rest
            of them — which leaves this row to do the one job it is named for. */}
        {/* outside the tab scroller (`flex: none`), so it never scrolls out of reach */}
        {p.onCollapse && <CollapseButton side="right" onClick={p.onCollapse} label={p.collapseLabel} />}
      </div>
      <div className="vx-pane vx-pages" data-pane="pages" style={{ gridRow: 2, gridColumn: 3 }}>
        {/* The rail sits INSIDE the panel, under the shared header band — beside the open file, the
            way the founder's reference has it. `onOpenTab` is this panel's own open route, so an
            explicit open-in-tab lands on the chat record exactly like a link click does; a plain
            click never comes through here at all (decision 28). */}
        {navShown && <Navigator onOpenTab={p.onOpen} onClose={() => showNav(false)} reveal={navReveal} />}
        <div className="vx-pages-col" hidden={canvas && navShown}>
        {/* WHAT is in front, and what can be done to it — the document's own header row.
            Filename prominent, location subdued beside it, every utility grouped hard right
            (founder reference, the desktop app's doc panel). Three rows now stack above the body
            and each answers a different question: the tabs say what is OPEN, this says what is IN
            FRONT, the crumb below says where it LIVES and walks you back up.
            A canvas is exempt — it names its own meeting in its own header, and there is no file
            here to read as source, copy or edit, so the whole row (not just the group) stands down. */}
        {/* A PAGE OPENS WITH ITS TITLE AND ITS CONTENT (UI decisions 12): no name row and no band of
            icons above it. Copy, Edit, Details, Show in Files and Delete are one "…" at the page's
            corner; editing keeps its own compact row (Cancel · Save) while it lasts. */}
        {doc && mode === "edit" && <div className="vx-doc-head">
          <span data-doc-name title={docName} className="vx-doc-name">{docName}</span>
          <span className="vx-panelhead-gap" />
          {saveError && <span data-doc-act="save-error" role="alert" title={saveError} className="vx-doc-error">Could not save: {saveError}</span>}
          <Button data-doc-act="cancel" variant="ghost" onClick={() => { setSaveError(null); setMode("view"); }} title="Cancel">Cancel</Button>
          <Button data-doc-act="save" variant="primary" onClick={() => void save()} disabled={saving} loading={saving} title="Save">{saving ? "Saving…" : "Save"}</Button>
        </div>}
        {/* the breadcrumb — the doc's address, and a path you can walk back up. A canvas has no
            address: its `path` is a row id, and the canvas names the meeting in its own header. */}
        {/* ONE NAME (founder ruling 2026-09-06: *"no need to duplicate doc name"*). The header
            directly above already says which file is in front; this row ended in the same string,
            so the screen said it twice. What is left is the FOLDER TRAIL — the question this row
            answers, and the only part of it you can click. A folder LISTING has no header above it,
            so there the last segment is the folder you are standing in and it stays. */}
        {/* ONE BREADCRUMB STYLE (guidelines §4.7): the ui-kit Breadcrumb — sans, chevron
            separators, ancestors quiet, and the middle folded into "…" once the trail is long, so
            it never scrolls the panel sideways. Names come from `crumbLabel` (never a number). */}
        {!canvas && !doc && !home && <div title={fullPath} data-crumb className="vx-doc-crumb">
          <Identity kind="workspace" id={slug ?? "personal"} slug={slug ?? "personal"} name={workspaceLabel(slug)} size={16} className="vx-id-inline" />
          <Breadcrumb label="Where this page lives" items={[
            ...trail.map(({ i, label: c }) => ({ key: `c${i}`, label: c as string, onSelect: () => nav(i) })),
            ...(!doc ? [{ key: "leaf", label: leaf as string }] : []),
          ]} />
        </div>}
        <div ref={docBox} data-doc-body className="vx-doc-body" data-flush={canvas || (mode === "edit" && !listing) ? "" : undefined} data-canvas={canvas ? "" : undefined}>
          {doc && !p.notice && p.body !== null && mode === "view" && (
            <span className="vx-doc-more">
              <Menu label="Page actions" align="end" triggerData={{ "data-doc-more": "" }}
                trigger={<MoreHorizontal size={15} strokeWidth={1.75} aria-hidden />}
                items={[
                  { key: "copy", label: copied ? "Copied" : "Copy contents", data: { "data-doc-act": "copy" }, onSelect: () => { void copyText(p.body ?? ""); setCopied(true); } },
                  { key: "edit", label: "Edit", data: { "data-doc-act": "edit" }, onSelect: () => {
                    // A MEETING NOTE'S MARKERS ARE NEVER SHOWN (#1783): the editor gets the words, the
                    // save puts the frontmatter, transcript marker and region fences back.
                    const e = editableNote(p.body ?? "");
                    restoreRef.current = e.restore; setDraft(e.text); setSaveError(null); setMode("edit"); } },
                  { key: "details", label: details ? "Hide details" : "Details", data: { "data-doc-act": "details" }, onSelect: () => setDetails((v) => !v) },
                  // A PAGE MADE THE MEETING'S PAGE (#1783): bound by identity, never by a title
                  ...(isMeetingNotePath(p.docPath) ? [{ key: "attach-meeting", label: "Use as meeting page…", data: { "data-doc-act": "attach-meeting" }, onSelect: () => setAttaching(true) }] : []),
                  { key: "files", label: `Show in Files · ${fullPath}`, data: { "data-doc-act": "where" }, onSelect: () => showNav(true) },
                  ...(canShow ? [{ key: "show-everyone", label: "Show to everyone in the call", data: { "data-doc-act": "show-everyone" },
                    onSelect: () => { void showPage(inCall!.session, { workspace: p.docSlug!, path: p.docPath }); } }] : []),
                  ...(canShowRoom ? [showingThis
                    ? { key: "show-room-stop", label: "Stop showing to the room", data: { "data-doc-act": "show-room-stop" },
                        onSelect: () => { void stopRoomShow(p.roomSession!, { workspace: p.docSlug!, path: p.docPath }).catch(() => toast("Couldn't stop showing it")); } }
                    : { key: "show-room", label: "Show to room", data: { "data-doc-act": "show-room" },
                        onSelect: () => { void showPage(p.roomSession!, { workspace: p.docSlug!, path: p.docPath }).then(() => window.dispatchEvent(new CustomEvent(ROOM_LINES_REFRESH))); } }] : []),
                  ...desktopItems({ kind: "file", slug: p.docSlug, path: p.docPath }).map((it) => ({
                    key: it.id, label: it.label, data: { "data-doc-act": it.id }, onSelect: () => { void it.onSelect(); } })),
                  // Make a card (desktop ADR D2, 15c/15d): a CRM card about this page
                  ...(!docMeeting ? cardItems({ kind: "page", slug: p.docSlug, path: p.docPath, title: pageTitle(p.docPath) })
                    .map((it) => ({ key: it.id, label: it.label, data: { "data-doc-act": "make-card" }, onSelect: () => { void it.onSelect(); } })) : []),
                  ...(mayDelete && !docMeeting && !isWorkspaceReadme(p.docPath) ? [{ key: "delete", label: "Delete page…", danger: true, separatorBefore: true,
                    data: { "data-doc-act": "delete" }, onSelect: () => setDeleting({ path: p.docPath, slug: p.docSlug, title: pageTitle(p.docPath), workspace: workspaceLabel(p.docSlug) }) }] : []),
                ]} />
            </span>
          )}
          {p.notice && !canvas
            ? <div data-pages-notice className="vx-doc-notice">{p.notice}</div>
            : canvas
            // the canvas owns its own scrolling, header and padding — it is a whole surface, not a body
            ? (MeetingCanvas
                ? <MeetingCanvas id={`meeting:${p.docPath}`} params={{ meetingId: p.docPath }} active />
                : <EmptyState>The meeting surface is not registered in this build.</EmptyState>)
            // home2 (R3): a workspace's own page, built from components (the README stays a document)
            : richOn && isWorkspacePagePath(p.docPath)
              ? <WorkspacePage slug={p.docSlug} />
            : home
              ? <ChatArtifactList items={p.artifacts ?? []} onOpen={(a) => p.onArtifact?.(a)} onAllFiles={() => showNav(true)} />
            : listing
              ? <FolderListing listing={listing} onNavigate={p.onNavigate} onOpen={p.onOpen} />
              // AN IMAGE FILE IS A PICTURE, never its bytes as text (founder: "you need to render svg files")
              : p.docPath && isImageFile(p.docPath)
                ? <ImageViewer path={p.docPath} slug={p.docSlug} source={p.body} />
              : p.body === null
                ? <div className="vx-doc-empty">
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
                  /* THE HEADER OWNS THE TITLE (Vexa-ai/vexa#1634's design spec, point 2, applied
                     in #1642): on a workspace README the first heading is LIFTED out of the body
                     and rendered above the eyebrow's two rows, so the page reads eyebrow → title →
                     who is here → what last changed → hairline → prose. Rendering it in both
                     places was the alternative, and a title printed twice two lines apart is the
                     shape a person reads as a bug. Every other page is untouched. */
                  : <TableRendererContext.Provider value={richOn ? LiveTable : null}><DocMetaContext.Provider value={{ path: p.docPath, slug: p.docSlug }}>
                      {isWorkspaceReadme(p.docPath)
                        ? (() => {
                            const front = splitLeadingH1(p.body);
                            return <>
                              {/* the path line a plain page has, with the file named (founder 2026-10-15) */}
                              <PagePath path={p.docPath} slug={p.docSlug} reads={p.chatReads} file
                                onFolder={(dir) => { setNavReveal({ slug: p.docSlug, dir, at: Date.now() }); showNav(true); }} />
                              <WorkspaceReadmePanel slug={p.docSlug} path={p.docPath} title={front.title} />
                              {richOn && hasRichSyntax(front.body)
                                ? <RichPage slug={p.docSlug} path={p.docPath} md={front.body} />
                                : <MdxDoc>{front.body}</MdxDoc>}
                            </>;
                          })()
                        : (() => {
                            // A PLAIN PAGE (UI decisions 12): source tails as markers listed once at
                            // the bottom, empty headings not drawn, the property table under Details.
                            // THE TITLE AND WHERE IT LIVES, above the prose (founder, live: the page
                            // started with its first paragraph and "does not say what its path is")
                            const head = pageHeadTitle(p.body ?? "", p.docPath ?? "");
                            const pp = plainPage(head.body);
                            return <>
                              {p.docPath && <PageHead title={head.title} path={p.docPath} slug={p.docSlug} reads={p.chatReads}
                                onFolder={(dir) => { setNavReveal({ slug: p.docSlug, dir, at: Date.now() }); showNav(true); }} />}
                              {/* home2 (R3): a page using the rich grammar is drawn live; a plain one as before */}
                              {richOn && hasRichSyntax(pp.body)
                                ? <RichPage slug={p.docSlug} path={p.docPath} md={pp.body + sourcesBlock(pp.sources)} />
                                : <MdxDoc properties={details}>{pp.body + sourcesBlock(pp.sources)}</MdxDoc>}
                            </>;
                          })()}
                    </DocMetaContext.Provider></TableRendererContext.Provider>}
          {/* EXTEND, UNDER THE CONTENT (decision 32.1, as ruled 2026-09-06). Only while READING a
              document that exists: an empty page offers Create instead, and a canvas has no page to
              extend at all. */}
          {/* …never on a meeting's note file (UI decisions 9): the meeting's sidebar is where it grows */}
          {doc && !p.notice && p.body !== null && mode === "view" && !docMeeting && (
            <ExtendPageButton workspace={p.docSlug} path={p.docPath} />
          )}
          {/* PRD decision 32.1's second trigger. Only while READING — an editor's selection is
              being edited, not asked about. */}
          {doc && !p.notice && p.body !== null && mode === "view" && (
            <SelectionExtend containerRef={docBox} workspace={p.docSlug} path={p.docPath} body={p.body} meeting={docMeeting} />
          )}
        </div>
        </div>
      </div>
      {deleting && <DeletePageDialog target={deleting} onClose={() => setDeleting(null)} />}
      {attaching && <AttachMeetingDialog path={p.docPath} workspace={p.docSlug ?? ""} onClose={() => setAttaching(false)}
        onAttached={() => p.onSaved?.()} />}
      <PageRemovedToast />
    </>
  );
}

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
  // A WORKSPACE WITH NO FRONT PAGE (#1783): its root offers to make one — a desktop tile lands here
  // when there is no README to open. The server refuses where the reader may not write; it says so.
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const noReadme = !prefix && !p.listing.files.some((f) => isWorkspaceReadme(f));
  const createReadme = async () => {
    setCreating(true); setCreateError(null);
    try {
      await writeWorkspaceFile(WORKSPACE_FRONT_PAGE, "# README\n", slug ? { slug } : undefined);
      p.onOpen({ path: WORKSPACE_FRONT_PAGE, slug, label: "README" });
    } catch {
      setCreateError("The README could not be created here.");
    } finally {
      setCreating(false);
    }
  };
  const create = noReadme && (
    <div className="vx-listing-create" data-create-readme="">
      <Button variant="primary" onClick={() => void createReadme()} disabled={creating} loading={creating}>Create README</Button>
      {createError && <span role="alert" className="vx-doc-error">{createError}</span>}
    </div>
  );
  if (!dirs.length && !files.length) {
    return <>{create}<EmptyState>Nothing in this folder.</EmptyState></>;
  }
  return (
    <div className="vx-listing">
      {create}
      {dirs.map((d) => (
        <button key={"d/" + d} data-entry="dir" className="vx-row2-main vx-listing-entry" onClick={() => p.onNavigate?.(slug, at(d))}>
          <Folder size={14} strokeWidth={1.75} aria-hidden /><span className="vx-row2-title">{d}/</span></button>
      ))}
      {files.map((f) => (
        <button key={"f/" + f} data-entry="file" className="vx-row2-main vx-listing-entry" data-file=""
          onClick={() => p.onOpen({ path: at(f), slug, label: f.replace(/\.md$/i, "") })}>
          <FileText size={14} strokeWidth={1.75} aria-hidden /><span className="vx-row2-title">{f}</span></button>
      ))}
    </div>
  );
}

/** WHAT THIS CHAT'S AGENT MADE, newest first — "Wrote Sponsor plan · 10:12". One line each; a click
 *  opens it. "All files" is the workspace browser, one click deeper and never the default. */
/** A file the chat made, as the desktop places it — a page in a workspace; nothing else is a file. */
const artifactTarget = (a: ChatArtifact): DesktopTarget | null =>
  a.page && a.page.kind !== "meeting" && a.page.path ? { kind: "file", slug: a.page.slug, path: a.page.path } : null;

function ChatArtifactList(p: { items: ChatArtifact[]; onOpen: (a: ChatArtifact) => void; onAllFiles: () => void }) {
  const desktopItems = useDesktopItems();
  const [menu, setMenu] = useState<{ key: string; x: number; y: number } | null>(null);
  return (
    <div className="vx-listing" data-chat-artifacts="">
      {p.items.length === 0 && <EmptyState>Nothing from this chat yet. What the agent writes or opens here is listed here.</EmptyState>}
      {p.items.map((a) => {
        const t = artifactTarget(a);
        const items = t ? desktopItems(t) : [];
        return (
          <div key={a.key} className="vx-row2 vx-listing-row">
            <button data-entry="artifact" data-artifact-kind={a.kind} className="vx-row2-main vx-listing-entry" onClick={() => p.onOpen(a)}>
              <FileText size={14} strokeWidth={1.75} aria-hidden />
              <span className="vx-row2-title">{artifactVerb(a.kind)} {a.title}</span>
              {a.at ? <span className="vx-row2-meta">{artifactTime(a.at)}</span> : null}
            </button>
            {items.length > 0 && <span className="vx-row2-actions" data-open={menu?.key === a.key ? "" : undefined}>
              <IconButton label={`More for ${a.title}`} size="xs" aria-haspopup="menu" data-artifact-more={a.key}
                onClick={(e: ReactMouseEvent<HTMLButtonElement>) => { const b = e.currentTarget.getBoundingClientRect(); setMenu({ key: a.key, x: b.right - 188, y: b.bottom + 4 }); }}>
                <MoreHorizontal size={14} strokeWidth={1.75} />
              </IconButton>
            </span>}
            {menu?.key === a.key && <ContextMenu x={menu.x} y={menu.y} label={`Actions for ${a.title}`} items={items} onClose={() => setMenu(null)} />}
          </div>
        );
      })}
      <button data-entry="all-files" className="vx-row2-main vx-listing-entry" onClick={p.onAllFiles}>
        <Folder size={14} strokeWidth={1.75} aria-hidden /><span className="vx-row2-title">All files</span></button>
    </div>
  );
}

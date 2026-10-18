"use client";
/** MINUTES — the shell (#1311). Design source: biz drafts/minutes-mock-chat.
 *
 *  A CHAT is the saved focus state: a label, an optional meeting ref, the workspaces it is over,
 *  and the artifacts it opened with. The rail lists chats and nothing else — projects are gone as a
 *  concept, and a meeting is simply a chat that names one.
 *
 *  A WORKSPACE is a folder — the shared thing. It is still what a chat mounts, and the header still
 *  names the mount set, but this surface no longer MANAGES one (founder ruling: "remove workspaces,
 *  they can do that via MCP if they need"): creating, inviting, resetting and deleting a folder are
 *  MCP verbs and conversation, so the setup buttons, the inventory and the delete ceremony are gone
 *  from here. Personal onboarding still has its own entry — app/OnboardingGate.tsx fires the seed.
 *
 *  One CSS grid: three columns (rail · conversation · pages), a shared 46px header band. */
import { ROUTINES_CHANGED_EVENT } from "./RoutineBar";
import { COMPANY_WORD, WORKSPACE_WORD, deskName } from "./vocabulary";
import { closeCentre, useCentre } from "./shell2/centre";
import { CentrePane } from "./shell2/CentrePane";
import { setMe } from "./shell2/me";
import { isPersonPath } from "./shell2/personOpen";
import { BottomBar } from "./shell2/BottomBar";
import { StripNav } from "./shell2/StripNav";
import { useBackCloses } from "./shell2/useBackCloses";
import { CHAT_ABOUT_EVENT, consumeKeepPanel, rememberAbout, setKnownChats, type Artifact as AboutArtifact } from "./shell2/about";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ARTIFACT_EVENT, CHOOSE_TARGET_EVENT, CHAT_ARTIFACTS_EVENT, ASK_CHAT_EVENT, CALL_PLACED_EVENT, CALL_START_EVENT, CHAT_TOUCHED_EVENT, OPEN_SESSION_EVENT, FOCUS_WORKSPACE_EVENT, WORKSPACE_COMMIT_EVENT, OPEN_ENTITY_EVENT, PAGE_REMOVED_EVENT, OPEN_MEETING_EVENT, OPEN_PAGE_EVENT, OPEN_WORKSPACE_FILES_EVENT, useService } from "../platform";
import { LEVEL_SET_EVENT, NEW_CHAT_AT_LEVEL_EVENT } from "../platform";
import { Chat } from "../surfaces/chat";
import { ensureMeetingKnown, refreshMeetings, useLiveMeetings } from "../surfaces/liveMeetings";
import { browserVoiceNoteDeps, startVoiceNote, voiceNoteState, VOICE_NOTE_OPEN } from "./voiceNote";
import {
  setChatTarget, getDefaultTarget, setDefaultTarget, readWorkspaceFile, readDeskFacts, type DeskFacts,
  listSharedMemberships, listWritableWorkspaces, listWorkspaceTree, readWorkspaceById, readWorkspaceBySlug, type Membership, type WritableWorkspace,
} from "../data/workspaceApi";
import { AttachRepo } from "./AttachRepo";
import { FolderImportHost } from "../surfaces/FolderImport";
import { requestFolderImport } from "../surfaces/folderDrop";
import { WorkspaceName } from "../ui-kit/WsLink";
import { moveChat } from "./chatOrder";
import { currentUserPins, loadUserPins, pinPage, unpinPage, useUserPins, withUserPins, pinsForPanel } from "./userPins";
import { RailToast } from "./RailToast";
import { fetchMarks, markRow, type RailMark, type RailMarks } from "./railMarks";
import { applyFolders, createFolder, deleteFolder, fetchFolders, foldersFromMarks, FoldersUnavailable, HIDDEN, moveRows, NO_FOLDERS, PINNED, UNGROUPED, updateFolder, type RailFolders } from "./railFolders";
import { ContextBar, GLOBAL_MOUNT } from "./ContextBar";
import { featureOn, useFeature } from "../platform/features";
import { workspaceLabel } from "../ui-kit/wsLinks";
import { RoomHeader } from "./rooms/RoomHeader";
import { MessageActionsHost } from "./readme/ObjectActions";
import { BrowseRoomsDialog, NewRoomDialog } from "./rooms/RoomDialogs";
import { leaveRoom } from "../surfaces/roomsApi";
import { refreshRoom, useRoom } from "../surfaces/roomState";
import { Face, thinkOf, rememberThink } from "./rooms/Face";
import { useAutoTitles } from "./rooms/autoTitles";
import { Modal } from "../ui-kit/Modal";
import { notifyScope } from "../surfaces/scopeEvents";
import { autoOpens, dropStaleTabs, openingStrip, panelShows } from "./staleTabs";
import { chatArtifacts, writesOf, type ChatArtifact } from "./chatArtifacts";
import type { ScopeView } from "../data/workspaceApi";
import { ChatRoutines, retireRoutine, routineOfOpenChat } from "./RoutineBar";
import { OPEN_CHAT_EVENT } from "../surfaces/agent-window";
import { HOME_TARGET_EVENT, NEW_CHAT_AT_EVENT, NEW_ROOM_EVENT, START_MEETING_EVENT } from "../platform/events";
import { NEW_WORKSPACE_KICK } from "./desktop/home2/StatusCard";
import { PERSONAL as HOME_PERSONAL, aimHome, homeTargetAct, noteHomeTarget, setHomeFocus } from "./desktop/home2/HomeTarget";
import { WORKSPACE_PAGE_WIDE_EVENT, isWorkspacePagePath } from "./desktop/home2/workspaceView";
import { ConnectionsPanel, CONNECTIONS_CLOSE } from "./ConnectionsPanel";
import { dropRemovedPage } from "./pageDelete";
import { BrowserSessionView } from "./BrowserSessionView";
import { PagesPanel, type Listing } from "./PagesPanel";
import { withRoutineState,
  bindMeeting, chatForRow, chatsFromSessions, clearHidden, dropEmptyUnlisted, dropLeftShared, legacyShellStore, loadChats, loadHidden, markTouched,
  meetingChatId, meetingTitle, mergeChats, nameChat, nameFromTurn,
  insideWorkspace, newChat, railRows, readRailOwner, resetChats, setTarget, withDefaultTarget, writeRailOwner,
  removeChat, saveChats, upsertChat, artifactKey,
  forgetHistory, orderHistory, stripForRecord, togglePinned, touchHistory,
  type Artifact, type Chat as ChatRec, type Row, closingTab } from "./chats";
import { addParticipant, bindMeetingToChat, listParticipants, listSessions } from "../surfaces/sessionsApi";
import { LiveMeetingChip, meetingToOpen } from "./ChatMeetingStrip";
import { pinLive } from "./livePin";
import { OPEN_MEETING_CHAT } from "./MeetingPageHeader";
import { meetingActs, meetingStatusOf, sendBotAgain, stopBot } from "./meetingActions";
import { HomeStream } from "./HomeStream";
import { DesktopHome, DesktopToaster } from "./desktop/DesktopHome";
import { PeopleStrip } from "./PeopleStrip";
import { cardTitle, directChatId, expandCard, externalMessagePrompt, homePrompt, personPagePrompt, readHome, sendFeedback, type HomeRead } from "./homeApi";
import { BlocksProvider, cardPeople, homeFirstVisit, patchHome, peekFromMarkdown, type BlocksHandlers, type BlockCard as Card, type BlockPerson as Person } from "../ui-kit";
import { sendMeetingAct } from "./MeetingActionsBar";
import { useMeetingDeletions, wasDeletedHere } from "./meetingDeletion";
import { exportable } from "../surfaces/MeetingBundleActions";
import { createMeetingNotes, updateMeetingNotes } from "./meetingNote";
import { pauseVoiceNote as pauseNote, stopVoiceNote as stopNote, voiceNoteState as noteState } from "./voiceNote";
import type { ContextMenuItem } from "../ui-kit/ContextMenu";
import { NotificationToasts } from "./NotificationToasts";
import { HOME_FRAME_EVENT, RAIL_CHANGED_EVENT, startRailFrames } from "./notifications";
import { metaOfOpen, resolveDocRef } from "../ui-kit/docLinks";
import { SURFACE_RECORD_LIVE, syncSurface } from "../surfaces/surfaceSync";
import { Rail, RailStrip } from "./Rail";
import { ScaffoldRefusalCard } from "./ScaffoldRefusalCard";
import { meetingPhase, type MeetingMock } from "../surfaces/meetingModel";
import { scaffoldToChat, type Scaffold, type ScaffoldRefusal } from "./scaffold";
import { pendingArrival, useScaffoldArrival } from "./arrival";
import { chatForDeepLink, resolveWorkspaceDeepLink, type DeepLinkOutcome } from "./deepLink";
import { landsOnHome, stashedDeepLink } from "./landOnHome";
import { homeChips } from "./desktop/home2/workspaceNoise";
import { SHARED_ARRIVAL_KEY as ARRIVAL_KEY_FOR_HOME } from "./roomView";
import { workspaceRouteFromPath, type WorkspaceRoute } from "../platform/workspaceRoute";
import { artifactsFromTokens, artifactViewEffect, isRetiredNotePath, meetingPages, pageForArtifact, pageForDocRef, meetingRefPage, pageForMeetingRef, panelForChat, pagesForPhase, resolveView, meetingChatFront, withMeetingPages, withoutRetiredMeetingDocs, VIEW_KEY, VIEW_NAVIGATE_EVENT, type ViewSlot , SHARED_ARRIVAL_KEY, sharedArrivalFront } from "./roomView";
import { fetchMeetingNotePath } from "./meetingNote";
import { deskPanelPages } from "./deskPanel";
import { reportOpened } from "./deskTouch";
import { startersFor, applyProposal, CALENDAR, proposals, type ConnectionRow, type Proposal, type Read, type UpcomingEvent } from "./proposals";
import { CONNECTIONS_OPEN } from "./connectionEvents";
import { listProposals, resolveProposal, type DeskProposal } from "../surfaces/proposalsApi";
import { ProposalChips } from "./ProposalChips";
import { AdminHome } from "./AdminHome";
import { EdgeHandle, ShellToggle } from "./Collapse";
import { Drawer, Sheet, Splitter, useShellLayout } from "../ui-kit";
import { CallDock } from "./CallDock";
import { enterCall } from "./callState";
import { joinPlacedCall, livePlacedDeps } from "./callPlaced";
import { browserSessionFromOpenPath, openBrowserSession } from "./browserSession";
import { MOCK_CHATS, MOCK_MEETINGS, mockBody, mockOn } from "./mockPhases";
import { T, surface, type as ty } from "./tokens";
import { LayoutServiceId } from "../platform/layout";
import type { Page, Sel } from "./types";

/** A record → the selection that shows it. There is no default selection any more: the shell opens
 *  on a chat that EXISTS, or on a draft it just minted (see `firstOpen`). It used to open on a
 *  hard-coded "Personal" row — an id the rail planted — and that row is exactly what F34 deletes. */
const selOf = (c: ChatRec): Sel => ({
  kind: c.meeting ? "meeting" : "chat",
  chatId: c.id,
  meetingId: c.meeting,
  label: c.label,
  workspaces: c.workspaces,
  target: c.target,
});

/** Say the chip's line into ONE chat. The settle delay is not cosmetic — the target session must be
 *  mounted and listening or the ask goes to localStorage and waits — and the session id is carried
 *  so it can never land in whichever conversation happens to be visible. `say` is the visible form
 *  for a chip whose words are the user's own; without one the turn arrives hidden, as system kickoffs
 *  always have. */
const fireKick = (session: string, prompt: string, say?: string, scaffoldId?: string) =>
  window.setTimeout(() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT,
    // `scaffoldId` rides with the FIRST turn so dispatch can read the same record the panel
    // rendered from (PRD §5.5: one record, two renderers). Without it the server would have to
    // re-derive mounts and opening from the session, which is the composed-from-whatever-was-there
    // problem the scaffold exists to end.
    { detail: { hidden: !say, display: say, session, prompt, scaffoldId } })), 1200);

/** The suggestion row could not read what it is built from. Said ONCE per page — the row re-reads
 *  on every focus, and a console that repeats the same line every time the tab comes back says
 *  nothing new. The row itself shows no chips (or no prep chip) instead of a wrong one. */
let chipFaultLogged = false;
function logChipFault(e: unknown) {
  if (chipFaultLogged) return;
  chipFaultLogged = true;
  console.warn(JSON.stringify({ event: "suggestions_unreadable", source: "terminal", detail: String((e as Error)?.message ?? e) }));
}

export function MinutesShell() {
  const layout = useService(LayoutServiceId);
  const realMeetings = useLiveMeetings();
  // `?mock=1` — three fabricated meetings, one per phase, plus a handful of never-touched
  // auto-created chats so the rail's FILTER can be judged before the data exists. Off unless the
  // flag is set; see mockPhases.ts.
  const mock = mockOn();
  const mockStart = useRef(Date.now());
  const meetings = useMemo(() => (mock ? [...MOCK_MEETINGS, ...realMeetings] : realMeetings), [mock, realMeetings]);
  // THE STORED LIST, AND NOTHING IS ADDED TO IT (founder ruling 2026-09-02, F34).
  //
  //  The rail used to PLANT two rows — "Personal" and "Organisation setup" — and a company-layer
  //  hint decided when to plant them. The founder opened his rail, found three chats he had never
  //  made, and asked the only question that matters: *"where is it coming from? i did not create
  //  this chat, and i do not like this text."* So the seeding is gone, and with it the hint that
  //  existed ONLY to time it (`companyLayerHint`, `COMPANY_LAYER_EVENT`, and both writers) — a
  //  cache nothing reads is the stale-code shape this same session ruled on.
  //
  //  The flicker that hint was introduced to fix is now fixed CORRECTLY: the rail renders nothing
  //  until it knows what exists, rather than planting rows to fill the gap. An empty rail for a
  //  moment is honest; a row nobody made is not, and it outlives the moment.
  //
  //  Mock chats are merged for DISPLAY only — they are never written back.
  const [chats, setChats] = useState<ChatRec[]>(() => loadChats());
  const allChats = useMemo(() => (mock ? [...chats, ...MOCK_CHATS] : chats), [mock, chats]);
  const chatsRef = useRef(allChats);
  useEffect(() => { chatsRef.current = allChats; }, [allChats]);
  // The meetings list polls, so anything that reads it from a LISTENER reads it here — a listener
  // rebuilt on every poll is a listener re-registered every few seconds.
  const meetingsRef = useRef(meetings);
  useEffect(() => { meetingsRef.current = meetings; }, [meetings]);

  // ── WHERE THE SHELL OPENS, and the DRAFT (founder ruling 2026-09-02, F35) ──────────────────
  //
  //  `+` DOES NOT WRITE A RECORD. The founder pressed it, never typed, and the row was still there
  //  after a reload — twice over: *"this chat was created with + but never used, it just should not
  //  exist."* So a new chat is EPHEMERAL until it has a human turn: it lives in `draft` below,
  //  storage is not touched, and leaving it, deleting it or closing the tab takes it with it
  //  because it was never anywhere else. The first human turn promotes it — one write, in the same
  //  moment it takes its name (F38), rather than a record now and a name later.
  //
  //  Deliberately NOT "write it and sweep it up later": a cleanup pass is a second writer of the
  //  same surface, and this workspace has measured what that costs. There is nothing to clean up
  //  when nothing was written.
  //
  //  The draft is NOT in `allChats`, so it is not a rail row either: the rail lists only chats a
  //  person opened or a scaffold composed, which is the other half of F34.
  //
  //  All three are decided in the FIRST RENDER so it already names a real session — the shell used
  //  to sit on a hard-coded selection until an effect ran, and the header advertised `personal`
  //  whatever the chat's real mount set was.
  // JTBD-1: whether the boot carried a link, read before any effect consumes its stash
  const [bootDeepLinked] = useState<boolean>(() => stashedDeepLink([ARRIVAL_KEY_FOR_HOME]));
  const [firstOpen] = useState<ChatRec>(() =>
    [...chats].sort((a, b) => (b.lastActivityAt || 0) - (a.lastActivityAt || 0))[0]
    ?? newChat("New chat", ["personal", "_global"], { touched: false }));
  const [draft, setDraft] = useState<ChatRec | null>(() =>
    (chats.some((c) => c.id === firstOpen.id) ? null : firstOpen));
  const draftRef = useRef<ChatRec | null>(draft);
  useEffect(() => { draftRef.current = draft; }, [draft]);
  const [sel, setSel] = useState<Sel>(() => selOf(firstOpen));
  // …and the same reason as `meetingsRef`: the artifact listener has to know which chat is in front
  // WHEN THE EVENT ARRIVES, and re-registering it on every selection change would be a listener that
  // can miss the event it exists for.
  const selRef = useRef(sel);
  useEffect(() => { selRef.current = sel; }, [sel]);
  const [pages, setPagesRaw] = useState<Page[]>([]);
  // PINNED PAGES ARE THE PERSON'S (`userPins.ts`, #1783): every update of this chat's tabs re-applies
  // them, so a pin made in any chat is a tab here, in the person's order, and an unpin is gone here.
  const userPins = useUserPins();
  const setPages = useCallback((next: Page[] | ((prev: Page[]) => Page[])) => {
    setPagesRaw((prev) => pinsForPanel(typeof next === "function" ? (next as (p: Page[]) => Page[])(prev) : next, currentUserPins(), featureOn("home2")));
  }, []);
  useEffect(() => { setPages((prev) => prev); }, [userPins, setPages]);
  // A LIVE MEETING IS ALWAYS PINNED: first in this strip in ANY chat while it runs (`livePin.ts`).
  const stripPages = useMemo(() => pinLive(pages, meetings), [pages, meetings]);
  // `?view=` — a chat's opening `artifacts[]` (the right-sidebar tabs), NOT a URL feature. A chat
  // is the saved focus state and a deeplink is just its constructor: the meeting says which pages
  // exist, this says which of them the chat opens with, and from then on the state is the chat's.
  // App.tsx cleans the URL on landing, so the spec arrives via localStorage; read it here (not
  // removed yet — a StrictMode double-render would eat it) and spend it on the FIRST room that
  // opens, which is the room `?meeting=` selected. In the full workbench the same key is read by
  // Workbench.tsx; only one of the two ever mounts, so the key keeps exactly one reader.
  const [pendingView] = useState<string | null>(() => {
    try { return localStorage.getItem(VIEW_KEY); } catch { return null; }
  });
  const viewSpent = useRef(false);
  // ── `/w/<workspace>/<path>` — A LINK NAMES A PAGE (Vexa-ai/vexa#1643) ──────────────────────
  //
  //  Read from the ADDRESS BAR, once, in the first render — the same reason every other deeplink
  //  key is read here: the effects below would otherwise race the boot for the first room. Unlike
  //  the others it is not a localStorage stash and it is not cleaned off the URL, because this
  //  route does not reload and the URL IS the durable statement: a refresh opens the same page.
  const [wsLink] = useState<WorkspaceRoute | null>(() => {
    try { return workspaceRouteFromPath(window.location.pathname); } catch { return null; }
  });
  /** The SERVER's answer about `wsLink` — open this page, or say this sentence. Null until it
   *  answers; the URL grants nothing on its own (`app/w/README.md`). */
  const [wsOutcome, setWsOutcome] = useState<DeepLinkOutcome | null>(null);
  const wsLanded = useRef(false);
  /** WHAT THE PANEL SAYS INSTEAD OF A DOCUMENT — a refusal's one sentence, and nothing else ever.
   *  The defect it answers is a link that opened the desk's README and said nothing at all. */
  const [notice, setNotice] = useState<string | null>(null);
  /** Has the chat list SETTLED — the server's sessions merged, or the attempt over? The landing
   *  waits for it, so a link can never open in a fresh draft while the reader's own chats are
   *  still in flight ("without starting a new chat when the viewer has chats"). */
  const [railSettled, setRailSettled] = useState(false);
  // Is a deeplink about to choose the first room? Read ONCE, here, before the effects that consume
  // these keys run — otherwise the boot below would race them and steal the room they were opening.
  const [deeplinkPending] = useState<boolean>(() => {
    // A `/w/` link is one of them: it chooses the chat AND the page, in `wsLink`'s own effects
    // below, so neither the boot nor the sessions landing may open a chat in front of it.
    if (wsLink) return true;
    try {
      return !!localStorage.getItem("vexa.openMeetingRef") || !!localStorage.getItem("vexa.pendingPreset") || !!localStorage.getItem(VIEW_KEY);
    } catch { return false; }
  });
  const [docPath, setDocPath] = useState("README.md");
  const [docSlug, setDocSlug] = useState<string | undefined>(undefined);
  // read by `startDraft` (a chat started from inside a workspace writes there, UI decision 6b)
  const docSlugRef = useRef<string | undefined>(undefined);
  docSlugRef.current = docSlug;
  // What KIND of thing is in front. A meeting tab is not a document: nothing is fetched for it and
  // the panel renders the meeting canvas instead of a body.
  const [docKind, setDocKind] = useState<"doc" | "meeting">("doc");
  const [docBody, setDocBody] = useState<string | null>(null);
  const [docNonce, setDocNonce] = useState(0);
  // a folder the breadcrumb navigated to — it takes over the panel body until a file is opened
  const [listing, setListing] = useState<Listing | null>(null);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  // Where this chat has BEEN. Session-level and per-chat: reading order is a property of the sitting,
  // not of the conversation, so unlike `artifacts[]` it is deliberately NOT persisted — reopening a
  // chat restores its documents, and starts your walk through them fresh.
  const [hist, setHist] = useState<{ stack: Artifact[]; i: number }>({ stack: [], i: -1 });
  useEffect(() => { void listSharedMemberships().then(setMemberships).catch(() => undefined); }, []);
  // EVERY WORKSPACE THIS READER CAN WRITE, by name (Vexa-ai/vexa#1783) — the Writes-to menu's
  // candidates, own private workspaces included. Read on mount and again whenever a turn makes or
  // targets a workspace (`focus`), so one the agent just created is in the menu without a reload.
  const [writable, setWritable] = useState<WritableWorkspace[]>([]);
  useEffect(() => {
    const load = () => { void listWritableWorkspaces().then(setWritable).catch(() => undefined); };
    load();
    window.addEventListener(FOCUS_WORKSPACE_EVENT, load);
    return () => window.removeEventListener(FOCUS_WORKSPACE_EVENT, load);
  }, []);
  // WHAT THIS PERSON'S DESK IS (Vexa-ai/vexa#1613) — the server's own three words, read off the
  // files. This used to probe `.scaffolded` and read its absence as "never set up", which on
  // 2026-09-06 offered the founder a workspace-setup chip over a desk that had been running for
  // forty minutes. Read ONCE, on mount: it is the only input the proposal row needs that is not
  // already in hand, and it costs one request. `null` until it answers, and null never offers the
  // chip (`proposals.needsSetup` fails closed).
  const [desk, setDesk] = useState<DeskFacts | null>(null);
  useEffect(() => { void readDeskFacts().then(setDesk).catch(() => undefined); }, []);
  // THE DESK'S SHORT LIST — the jobs other agents filed for this person (Vexa-ai/vexa#1614). One
  // plain read, on mount: `GET /api/proposals` is a file behind an identity check, not a turn, so
  // the row costs nothing to show and never asks a model what to offer. An empty answer (no desk, a
  // backend that did not reply) degrades to exactly the row this surface had before the store
  // existed — `listProposals` never throws, deliberately.
  const [deskItems, setDeskItems] = useState<DeskProposal[]>([]);
  useEffect(() => { void listProposals().then(setDeskItems); }, []);
  // THE PERSON'S OWN CONNECTIONS decide the row (founder, 2026-10-11: "connect email, calendar and
  // if connected is to check email and prepare for next meeting"). Read through the Connections
  // front door the panel uses (`/api/connections/list` → the broker, metadata only). Three states:
  // `null` until it answers, the rows, or `"error"` — and an error shows NO chips (never a guessed
  // row), logged once per page. Re-read when the tab regains focus and when a consent window reports
  // back, so a chip flips from "Connect …" to "Check my email" without a reload.
  const [connections, setConnections] = useState<Read<ConnectionRow[]>>(null);
  useEffect(() => {
    let live = true;
    const load = () => void fetch("/api/connections/list", { cache: "no-store" })
      .then(async (r) => {
        const d = r.ok ? await r.json() : null;
        if (!d || !Array.isArray(d.connections)) throw new Error(`connections list: HTTP ${r.status}`);
        return d.connections as ConnectionRow[];
      })
      .then((rows) => { if (live) setConnections(rows); })
      .catch((e) => { if (!live) return; logChipFault(e); setConnections("error"); });
    load();
    const onVis = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", onVis);
    const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("vexa:connection-consent") : null;
    if (channel) channel.onmessage = () => load();
    return () => { live = false; document.removeEventListener("visibilitychange", onVis); channel?.close(); };
  }, []);
  // THE NEXT MEETING ON THE CONNECTED CALENDAR — only asked for when a calendar is ready, and at most
  // once every five minutes (each read is a real, audited calendar call). The terminal projects it to
  // title + start; the row picks the soonest titled one (`nextMeeting`). An error here only costs the
  // prep chip — the rest of the row is still true.
  const [events, setEvents] = useState<Read<UpcomingEvent[]>>(null);
  const calendarReady = Array.isArray(connections) && connections.some((c) => c.provider === CALENDAR && c.status === "ready");
  useEffect(() => {
    if (!calendarReady) { setEvents(null); return; }
    let live = true;
    const load = () => void fetch("/api/connections/upcoming", { cache: "no-store" })
      .then(async (r) => {
        const d = r.ok ? await r.json() : null;
        if (!d || !Array.isArray(d.events)) throw new Error(`upcoming events: HTTP ${r.status}`);
        return d.events as UpcomingEvent[];
      })
      .then((ev) => { if (live) setEvents(ev); })
      .catch((e) => { if (!live) return; logChipFault(e); setEvents("error"); });
    load();
    const timer = window.setInterval(() => { if (!document.hidden) load(); }, 5 * 60 * 1000);
    return () => { live = false; window.clearInterval(timer); };
  }, [calendarReady]);
  // The signed-in address — the ONE fact the setup chip puts in the person's mouth, so they never
  // type what we already know. Same seam the account badge reads; unknown simply drops the clause.
  const [email, setEmail] = useState<string | null>(null);
  const speakerRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    let live = true;
    void fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!live) return;
        const who = ((d?.user as { email?: string } | undefined)?.email) ?? null;
        setEmail(who);
        // a voice note is the person's own voice: its lines carry their name, not the bot's "Speaker"
        const name = ((d?.user as { name?: string } | undefined)?.name ?? "").trim();
        // shell2: who "you" are, so the people surfaces say "you" and never list you
        const u = (d?.user ?? {}) as { id?: string | number; sub?: string };
        setMe({ name, email: who, subject: u.id != null ? String(u.id) : u.sub ?? "" });
        speakerRef.current = name || (who ? who.split("@")[0] : "") || undefined;
        // No identity means no server rail to wait for: the stored one is all there is, so the
        // list has settled. Without this a `/w/` link would wait forever for a fetch never made.
        if (!who) setRailSettled(true);
      })
      .catch(() => { if (live) setRailSettled(true); });
    return () => { live = false; };
  }, []);

  // THE RAIL BELONGS TO WHOEVER IS SIGNED IN. `vexa.minutes.chats` was one global key, so signing
  // in as a second person on the same browser showed them the FIRST person's rows — including
  // chats for meetings they have no access to. The identity cannot be known synchronously
  // (`vexa-user-info` is httpOnly), so the rail loads first and is checked here: same owner is a
  // no-op, an unstamped legacy rail is adopted, and a DIFFERENT owner's rail is dropped.
  const railOwnerChecked = useRef(false);
  useEffect(() => {
    if (!email || railOwnerChecked.current) return;
    railOwnerChecked.current = true;
    const owner = readRailOwner();
    if (owner === email) return;
    if (owner !== null) {
      // A rail that belongs to somebody else is dropped whole, and what replaces it is a DRAFT —
      // not a planted row. The new reader has opened nothing, so they have nothing.
      const d = newChat("New chat", ["personal", "_global"], { touched: false });
      setChats(resetChats());
      setDraft(d);
      setSel(selOf(d));
      setPages([]);
    }
    writeRailOwner(email);
  }, [email]);

  const [chatOrder, setChatOrder] = useState<string[]>([]);
  const [orderError, setOrderError] = useState("");
  const [orderBusy, setOrderBusy] = useState(false);
  useEffect(() => {
    let live = true;
    setChatOrder([]);
    fetch("/api/chat/order").then(r=>r.ok?r.json():Promise.reject()).then(d=>{if(live)setChatOrder(d.order || []);}).catch(()=>{});
    return ()=>{live=false;};
  }, [email]);
  // CHAT FOLDERS (`railFolders.ts`, #1783) — the person's own sections, read from the server so
  // every device and every reload shows the same rail: Pinned · their folders · Ungrouped · Hidden.
  // A server without the session store keeps no folders; the sections then come from the pin and
  // hide marks (`railMarks.ts`). The old browser-only delete list is migrated into Hidden here,
  // once: each id becomes a hidden mark, and an id the server does not know is simply gone.
  const [folders, setFolders] = useState<RailFolders>(NO_FOLDERS);
  const fromMarks = useCallback((m: RailMarks, order: string[]) => {
    const rank = new Map(order.map((k, i) => [k, i]));
    const f = foldersFromMarks(m);
    f.order[PINNED] = [...m.pinned].sort((x, y) => (rank.get(x) ?? 1e9) - (rank.get(y) ?? 1e9));
    return f;
  }, []);
  const refreshFolders = useCallback(async (order: string[] = []) => {
    try { setFolders(await fetchFolders()); return; }
    catch (e) { if (!(e instanceof FoldersUnavailable)) return; }
    try { setFolders(fromMarks(await fetchMarks(), order)); } catch { /* an unfiled rail */ }
  }, [fromMarks]);
  useEffect(() => {
    if (!email) return;
    let live = true;
    void (async () => {
      const tombstones = loadHidden();
      let migrated = true;
      for (const id of tombstones) {
        try { await markRow(`c:${id}`, { hidden: true }); }
        catch (e) { if (!String((e as Error).message).includes("404")) migrated = false; }
      }
      if (tombstones.length && migrated) clearHidden();
      if (live) await refreshFolders();
    })();
    return () => { live = false; };
  }, [email, refreshFolders]);
  // The agent files chats too (`chat_move`, `chat_folder_create` from the composer), so the folders
  // are read again while the tab is in front — the same way the chat list picks up new names.
  useEffect(() => {
    if (!email || !folders.served) return;
    const t = window.setInterval(() => { if (!document.hidden) void refreshFolders(); }, 10_000);
    return () => window.clearInterval(t);
  }, [email, folders.served, refreshFolders]);
  useEffect(() => {
    if (!folders.served && chatOrder.length) setFolders((f) => f.served ? f : { ...f, order: { ...f.order, [PINNED]: [...(f.order[PINNED] ?? [])].sort((x, y) => chatOrder.indexOf(x) - chatOrder.indexOf(y)) } });
  }, [chatOrder, folders.served]);
  // A meeting whose data was deleted leaves the lists (#1783): the server row says so after a
  // refresh (`artifacts_deleted`); this tab knows the moment its own delete answers.
  const deletions = useMeetingDeletions();
  const listedMeetings = useMemo(() => meetings.filter((m) => !m.artifacts_deleted && !wasDeletedHere(m.id)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [meetings, deletions]);
  // THE ROUTINES' OWN STATE, for the rail's badge (a retired routine's chat is a normal chat):
  // read on mount, every minute, and whenever a routine is paused, resumed or retired here.
  const [routineStates, setRoutineStates] = useState<{ name: string; enabled?: boolean; status?: string }[] | null>(null);
  useEffect(() => {
    let live = true;
    const read = () => void fetch("/api/routines", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (live && d && Array.isArray(d.routines)) setRoutineStates(d.routines); }).catch(() => {});
    read();
    const t = window.setInterval(read, 60_000);
    window.addEventListener(ROUTINES_CHANGED_EVENT, read);
    return () => { live = false; window.clearInterval(t); window.removeEventListener(ROUTINES_CHANGED_EVENT, read); };
  }, []);
  // R1 FLAGS: `rooms` — rooms made in a workspace, chats private; `home2` — the sidebar by workspace
  const roomsOn = useFeature("rooms");
  const home2 = useFeature("home2");
  // SHELL2 (0.13.4 UI-1): the approved shell — one-list sidebar, Inbox and Meetings in the centre,
  // My desk, outlined primaries, the phone fixes. Its CSS hangs off `data-shell2` on the root element
  // so menus, drawers and sheets (portals outside the shell) follow it too.
  const shell2 = useFeature("shell2") && home2;
  useEffect(() => {
    try { document.documentElement.toggleAttribute("data-shell2", shell2); } catch { /* no document */ }
  }, [shell2]);
  const centreList = useCentre();
  // focus belongs to the new chat it was set for: a stored chat coming to the front ends it
  useEffect(() => { if (draftRef.current?.id !== sel.chatId) setHomeFocus(false); }, [sel.chatId]);
  const pagesFullRef = useRef(false);
  useEffect(() => { if (shell2) setKnownChats(allChats.map((c) => ({ id: c.id, label: c.label }))); }, [shell2, allChats]);
  // RIGHT → LEFT (UI 7h): "Chat about this" — a chat in the centre with the artifact attached, the
  // panel holding its place; the artifact remembers the chat so the button then reads "Resume chat"
  useEffect(() => {
    const on = (e: Event) => {
      const a = (e as CustomEvent<AboutArtifact>).detail;
      if (!a?.key) return;
      const ws = a.slug && a.slug !== "personal" ? a.slug : "";
      const c = startDraftRef.current(ws);
      rememberAbout(a.key, c.id);
      // the new chat is the focused input for that artifact's workspace — not the whole home
      noteHomeTarget(ws || HOME_PERSONAL); setHomeFocus(true);
      window.setTimeout(() => {
        window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { mode: "draft", prompt: `About ${a.kind === "meeting" ? "the meeting" : "the page"} “${a.label}”${a.kind === "page" ? ` (${a.slug || "personal"}/${a.path})` : ""}: ` } }));
        window.dispatchEvent(new CustomEvent("vexa:terminal:focus-chat"));
      }, 80);
    };
    window.addEventListener(CHAT_ABOUT_EVENT, on);
    return () => window.removeEventListener(CHAT_ABOUT_EVENT, on);
  }, []);
  // THE SIDEBAR BY WORKSPACE (home2): Pinned · one group per workspace · Hidden, and no folders. A
  // chat lives in the group of the workspace it writes to; one with no workspace lives in Personal.
  const railFolders = useMemo<RailFolders>(() => {
    // UI 7e (founder 2026-10-16): chats are grouped by the person's FOLDERS, not by workspace
    if (!home2 || folders.served) return folders;
    const base = withRoutineState(railRows(allChats, listedMeetings), routineStates);
    const wss = [{ slug: "personal", name: deskName() }, ...writable.filter((w) => w.slug !== "personal")];
    const known = new Set(wss.map((w) => w.slug));
    const filings: Record<string, string> = {};
    for (const row of base) {
      if (folders.filings[row.key] === PINNED || folders.filings[row.key] === HIDDEN) { filings[row.key] = folders.filings[row.key]; continue; }
      const t = row.target && known.has(row.target) ? row.target : "personal";
      filings[row.key] = `ws:${t}`;
    }
    return { ...folders, filings, folders: [folders.folders.find((f) => f.id === PINNED) ?? { id: PINNED, name: "Pinned", builtin: true, collapsed: false },
      ...wss.map((w) => ({ id: `ws:${w.slug}`, name: w.name, builtin: false, collapsed: false })),
      folders.folders.find((f) => f.id === HIDDEN) ?? { id: HIDDEN, name: "Hidden", builtin: true, collapsed: true }] };
  }, [home2, folders, allChats, listedMeetings, routineStates, writable]);
  const rawRows = useMemo(() => applyFolders(withRoutineState(railRows(allChats, listedMeetings), routineStates), railFolders),
    [allChats, listedMeetings, railFolders, routineStates]);
  // the sidebar never shows "Chat 12:30": an unnamed chat takes its first sentence (home2)
  const rows = useAutoTitles(rawRows, home2, sel.chatId);
  const reorder = async (from: string, to: string) => {
    if (orderBusy) return;
    const next = moveChat(folders.order[PINNED] ?? [], from, to);
    setOrderBusy(true); setOrderError("");
    try {
      const res = await fetch("/api/chat/order", {method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({order:next})});
      if(!res.ok) throw new Error();
      setChatOrder(next);
    } catch { setOrderError("Couldn't save chat order. Try again."); }
    finally { setOrderBusy(false); }
  };
  const mark = async (row: string, m: RailMark) => {
    setOrderError("");
    try {
      if (folders.served) {
        const where = folders.filings[row];
        const to = m.hidden ? HIDDEN : m.pinned ? PINNED : UNGROUPED;
        if ((m.hidden === false && where !== HIDDEN) || (m.pinned === false && where !== PINNED)) return;
        setFolders(await moveRows([row], to));
      } else setFolders(fromMarks(await markRow(row, m), chatOrder));
    }
    catch { setOrderError(m.pinned !== undefined ? "Couldn't save the pin. Try again." : "Couldn't save. Try again."); }
  };
  const folderAct = async (act: () => Promise<unknown>, failed: string) => {
    setOrderError("");
    try { await act(); } catch (e) { setOrderError((e as Error).message || failed); }
    await refreshFolders();
  };
  const onMoveRows = (keys: string[], to: string, before?: string) => {
    setOrderError("");
    if (home2 && to.startsWith("ws:")) {
      // dropped on a workspace's group: the chat writes there from now on
      const slug = to.slice(3), target = slug === "personal" ? "" : slug;
      for (const k of keys) {
        const id = k.replace(/^c:/, "");
        persist((prev) => prev.map((c) => (c.id === id ? { ...c, workspaces: target && !c.workspaces.includes(target) ? [...c.workspaces, target] : c.workspaces, target: target || undefined } : c)));
        void setChatTarget(id, target).then(() => notifyScope()).catch(() => setOrderError("Couldn't move the chat. Try again."));
      }
      return;
    }
    void moveRows(keys, to, before).then(setFolders).catch((e) => setOrderError((e as Error).message || "Couldn't move the chat. Try again."));
  };
  const selKey = `c:${sel.chatId}`;
  // THE MEETING THIS CHAT STARTED (founder, 2026-10-13), for its header — a chat that is a
  // meeting's own (`meet-…`) already is that meeting's page and needs no strip.
  const boundMeetingId = sel.chatId.startsWith("meet-") ? "" : (allChats.find((c) => c.id === sel.chatId)?.meeting || "");
  const chatMeeting = boundMeetingId ? meetings.find((m) => String(m.id) === boundMeetingId) : undefined;
  /** The rail's meeting row menu: the page header's acts (`meetingActions.ts`). Acts that run on
   *  their own (stop, send again, notes, pause/stop a note) run here; the ones whose control is on
   *  the page (rename, language, share, export, delete with its confirmation) open the page and ask it. */
  const meetingActsFor = (r: Row): ContextMenuItem[] => {
    const m = meetings.find((x) => x.id === r.meetingId || x.native_id === r.meetingId);
    if (!m) return [];
    const vn = noteState();
    const recording = m.platform === "voice_note" && vn.meetingId === m.id && (vn.phase === "recording" || vn.phase === "starting") ? { paused: !!vn.paused } : null;
    const fail = (e: unknown) => setOrderError(e instanceof Error ? e.message : "Couldn't do that. Try again.");
    return meetingActs(m, { recording, canExport: exportable(m), canChangeLanguage: true, canRename: !m.shared }).map((a) => ({
      id: a.id, label: a.label, danger: a.danger,
      onSelect: () => {
        setOrderError("");
        if (a.id === "stop-bot") return void stopBot(m).then(refreshMeetings, fail);
        if (a.id === "send-again") return void sendBotAgain(m).then(refreshMeetings, fail);
        if (a.id === "pause" || a.id === "resume") return pauseNote(a.id === "pause");
        if (a.id === "stop-note") return void stopNote();
        if (a.id === "notes") return void createMeetingNotes(m.id).then((path) => { updateMeetingNotes(m.id, path); }, fail);
        void openRow(r).then(() => sendMeetingAct(m.id, a.id)).then((took) => { if (took === false) setOrderError("Open the meeting to do that."); });
      },
    }));
  };

  /** Persist a change to the stored list. Mock chats live outside it, so a mutation aimed at one is
   *  a no-op by construction — the mock is a display fixture, not a record. */
  const persist = useCallback((fn: (prev: ChatRec[]) => ChatRec[]) => {
    setChats((prev) => { const next = fn(prev); if (next !== prev) saveChats(next); return next; });
  }, []);

  // THE SHELL'S LAYOUT IS ONE PURE FUNCTION (terminal design guidelines §3.1, ui-kit/layout).
  // Both side columns still fold away by the reader's choice (founder, 2026-09-01) — that choice is
  // `prefs`, persisted — but what FITS is now the layout's call: as the window narrows the rail
  // folds to a strip and opens as a drawer, then the pages panel becomes a sheet, and the
  // conversation never shrinks below its floor. An auto-collapse never writes a preference, so
  // widening the window brings the reader's layout back. Widths are remembered per mode, clamped on
  // read and never discarded (the rule the old `vexa.minutes.pagesW` kept, carried over).
  const [attachTo, setAttachTo] = useState<{ id?: string } | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const shell = useShellLayout(shellRef, { legacy: legacyShellStore });
  const setPagesOpenRef = useRef(shell.setPagesOpen);
  setPagesOpenRef.current = shell.setPagesOpen;
  const L = shell.layout;
  // shell2 (mobile G2): system back closes the drawer or the pages sheet before it leaves the app
  useBackCloses(shell2 && L.railKind === "drawer" && shell.drawerOpen, () => shell.setRailOpen(false), "drawer");
  useBackCloses(shell2 && (L.pagesKind === "sheet" || L.pagesKind === "fullscreen") && shell.sheetOpen, () => shell.setPagesOpen(false), "sheet");
  const [connectionsMode,setConnectionsMode] = useState<'page'|'panel'|null>(null);
  const connectionsOpen=connectionsMode!==null;
  // `openPage` is a stable callback; it reaches the CURRENT layout controls through this ref.
  // Is the pages panel on screen — docked, or an open sheet? (The surface record reports it.)
  const pagesVisible = L.pagesKind === "docked" || shell.sheetOpen;
  const revealPages = useRef(shell.revealPages);
  revealPages.current = shell.revealPages;

  // OPENING A CHAT NO LONGER REWRITES THE PERSON'S WORKSPACES (Vexa-ai/vexa#1783). This used to
  // switch every shared workspace in the opened chat ON for the person and every other one OFF —
  // so the person-wide state followed whichever chat was opened last, a hidden workspace came back
  // the moment an old chat named it, and "the pile of workspaces" could not be put away. A chat's
  // reads are its own now (server-side, `workspace_state_set`), and the person's states change only
  // when they ask. Kept as a no-op so the call sites' order is unchanged.
  const mountSet = useCallback(async (_wanted: string[]) => { /* see above */ }, []);

  /** Open a chat. The ONE place a room's artifacts land.
   *
   *  A chat that has been read before REOPENS ITS OWN TABS — `artifacts[]` and `focus` are saved on
   *  the record, so leaving a conversation and coming back finds the same documents. A fresh chat
   *  starts from what the ROOM offers: the meeting's phase pages (prep vs has-transcript), or a
   *  README per mounted workspace. A pending `?view=` then seeds which artifact opens, exactly once.
   *
   *  Every setState below runs after the single `await`, so React commits them together — which is
   *  what lets the artifacts effect trust that `sel.chatId` and `pages` describe the same chat. */
  const openChat = useCallback(async (c: ChatRec, how: { front?: "meeting" } = {}) => {
    window.dispatchEvent(new Event(CONNECTIONS_CLOSE));
    // shell2: a conversation coming to the centre closes an open list (Inbox, Meetings)
    closeCentre();
    const m = c.meeting ? meetings.find((x) => String(x.id) === c.meeting) : undefined;
    // BOUND TO A ROW THIS CLIENT HAS NEVER LISTED — the bot the chat sent moments ago. Ask the list
    // for it instead of laying the room out as though the meeting did not exist; `pagesForPhase`
    // below reads `m`, so an unlisted row silently produced the wrong room AND a dead transcript.
    if (c.meeting && !m) ensureMeetingKnown(c.meeting);
    await mountSet(c.workspaces);
    const roomPages = async (): Promise<Page[]> => {
      if (c.meeting) {
        // ONE VIEW (founder-approved design, 2026-10-11). A real meeting opens the meeting view —
        // header, notes, transcript — in every phase, and the view reads its own notes from
        // `/api/meeting/note`, so laying the room out asks the server nothing and never depends on
        // the notes file existing. It is a property of the MEETING, never of the link that opened it.
        // A `?mock=1` meeting has no row behind it, so it keeps its canned markdown pages, keyed on
        // the composed path (`mockPhases.ts`) — the mock composes, and only the mock.
        const fake = mock && MOCK_MEETINGS.some((x) => String(x.id) === c.meeting);
        const native = (m as { native_id?: string } | undefined)?.native_id;
        const mockNote = fake && native ? `kg/entities/meeting/${native}.md` : null;
        return pagesForPhase(m ? meetingPhase(m) : "post", native, fake ? null : c.meeting, mockNote);
      }
      // THE DESK IS THE DEFAULT PAGE (PRD decision 26.4). This used to open the ORGANISATION's
      // README for a chat with no focus — the company's document, the same for everybody, and not
      // what a person opening a fresh conversation is looking at their screen to find out. And the
      // tabs are NAMED from the registry rather than by slug: this strip read `126` (F49).
      return deskPanelPages(c.workspaces, c.target ?? "");
    };
    /** THE MEETING'S OWN PAGES — the room's list, minus the personal page (Vexa-ai/vexa#1600).
     *
     *  They are wanted on the STORED path as well now, and that is the whole of the migration:
     *  `permanent` is a property of the PAGE, a strip written before the rule carries none, and the
     *  strips that exist are exactly the meeting chats somebody has used. `pagesForPhase` stays the
     *  one composer — these are the pages it marked as the meeting's own. The two paths are
     *  mutually exclusive, so this costs the note lookup once either way. */
    const meetingOwn = async (): Promise<Page[]> => (await roomPages()).filter((pg) => pg.permanent);
    // THE CHAT'S HOME LEADS THE STRIP (decision 28.5). Composed here rather than stored, so it
    // follows the chat's mounts if they change and can never be `×`-ed away — it is the product's
    // first entry, not something the reader put there.
    //
    // A ROOM'S OWN PAGES ARE DECLARED TABS, so they arrive PINNED (founder ruling 2026-09-06 —
    // *"no need to create tabs, unless there is a pinned tab"*). Nobody navigated to them; the room
    // laid them out, and with one preview slot an unpinned one would be evicted by the reader's
    // first click. A STORED strip keeps its own pin state instead: last session's preview page was
    // not a tab then and re-pinning it on load would put back the accumulation by the back door.
    // A STORED STRIP THAT HOLDS THE RETIRED NOTE PATH IS HEALED, ONCE (Vexa-ai/vexa#1588). Every
    // meeting chat opened before the fix pinned `kg/entities/meeting/<native>.md` — the spelling
    // this client composed — and a stored strip is otherwise replayed verbatim forever, so the
    // desks that hit the bug are exactly the ones the fix would never reach on its own.
    //
    // IT ONLY EVER REPLACES. When the server names a report, that entry points at it; when the
    // server names none, the strip is left exactly as the reader had it. Dropping the tab on a null
    // would be deciding, from an ABSENCE, that nothing is there — and `<native>.md` is a path an
    // agent turn can legitimately have written to on some desk. Nothing else in the strip is
    // touched either way: a reader owns their tabs, and this is the one entry no reader chose.
    const hasStrip = c.artifacts.length > 0;
    let strip = c.artifacts.map((a) => ({ ...a }));
    // THE TWO-TAB ERA'S NOTES TAB GOES (the one-view design, 2026-10-11): a meeting chat stored
    // before it pinned its notes file as a permanent "Brief"/"Minutes" tab beside the transcript,
    // and the view now renders that file itself. `withoutRetiredMeetingDocs` takes only what
    // `meetingPages` stamped; the reader's own tabs are untouched. A stored VIEW on that tab lands
    // on the meeting view instead (below), so the old name is never what a reader reopens onto.
    const retiredKeys = new Set<string>();
    if (hasStrip && c.meeting && !(mock && MOCK_MEETINGS.some((x) => String(x.id) === c.meeting))) {
      const kept = withoutRetiredMeetingDocs(strip);
      for (const a of strip) if (!kept.includes(a)) retiredKeys.add(artifactKey(a));
      strip = kept;
    }
    if (hasStrip && c.meeting) {
      const native = (m as { native_id?: string } | undefined)?.native_id;
      const stale = strip.findIndex((a) => a.kind !== "meeting" && isRetiredNotePath(a.path, native));
      if (stale >= 0) {
        const real = await fetchMeetingNotePath(c.meeting);
        if (real) strip = strip.map((a, k) => (k === stale ? { ...a, path: real } : a));
      }
    }
    //
    // A MEETING CHAT'S STORED STRIP IS TOLD WHICH OF ITS TABS ARE THE MEETING'S (Vexa-ai/vexa#1600),
    // and is given them back when it does not have them. That is not the "a reader owns their tabs"
    // rule being broken: under the founder's ruling the transcript is not a tab a reader may drop at
    // all, so a strip that dropped one under the OLD rule is not a decision there is anything to
    // preserve. It is also the only way the ruling reaches the desks it was made about — his own
    // included, since every meeting chat he has opened already has a strip on disk.
    // THE PANEL STAYS QUIET (UI decision 7 amended, founder 2026-10-15): a chat opens with the tabs
    // the person opened in IT — never an auto-opened desk or workspace README. A meeting chat keeps
    // its meeting's own pages (the meeting is the room).
    const base: Page[] = openingStrip(
      (c.meeting
        ? withMeetingPages(hasStrip ? strip : (await roomPages()).map((pg) => ({ ...pg, pinned: true })) as Artifact[], await meetingOwn())
        : strip) as Artifact[],
      { meeting: !!c.meeting },
    ) as Page[];
    let list = base;
    let focus: Page | null = c.focus ? base.find((pg) => artifactKey(pg) === c.focus) ?? null : null;
    if (!viewSpent.current && pendingView) {
      viewSpent.current = true;
      try { localStorage.removeItem(VIEW_KEY); } catch { /* locked-down storage */ }
      const r = resolveView(pendingView, base);
      list = r.pages; focus = r.focus ?? focus;
    }
    // The stored VIEW wins over the first tab: it is where the reader actually was. A chat with
    // tabs but no stored view (pre-28, or one that has never been navigated) still opens on its
    // focused tab, so nothing regresses for records written before the slot existed.
    const storedRaw = c.view ? { kind: c.view.kind, path: c.view.path, slug: c.view.slug, label: c.view.label } as Page : null;
    const stored = storedRaw && retiredKeys.has(artifactKey(storedRaw))
      ? (list.find((pg) => pg.kind === "meeting") ?? null)
      : storedRaw;
    // A MEETING CHAT OPENS ON ITS MEETING (the one-view design, 2026-10-11): the view is the same
    // wherever a meeting is opened, so a chat about one leads with it — unless the reader was last
    // on some other page of theirs, which is where they were and stays where they land.
    // ARRIVING THROUGH A SHARE puts the MEETING in front (#1801 `sharedArrivalFront`).
    const picked0 = sharedArrivalFront(list, how.front) ?? stored ?? focus ?? list[0];
    // THE PANEL FOLLOWS THIS CHAT (home2, founder 2026-10-16): nothing of its own → its target
    // workspace's page; no target → the panel stays closed.
    if (featureOn("home2") && !c.meeting) {
      const f = panelForChat({ list, front: picked0 ?? null, target: c.target, targetName: c.target ? workspaceLabel(c.target) : "" });
      list = f.list;
    }
    const picked = sharedArrivalFront(list, how.front) ?? stored ?? focus ?? list[0];
    const front = c.meeting ? meetingChatFront(list, picked) : picked;
    setSel({
      kind: c.meeting ? "meeting" : "chat",
      chatId: c.id,
      meetingId: c.meeting,
      label: c.label || (m ? meetingTitle(m) : "Chat"),
      workspaces: c.workspaces,
      target: c.target,
    });
    // shell2 (UI 7h/7i): A CHAT OPENED FROM THE RIGHT PANEL changes only the centre — the panel keeps
    // its view, scroll and open sections ("Chat about this", "Resume chat", a connected chat)
    if (featureOn("shell2") && consumeKeepPanel()) {
      setNotice(null); readerChoseFocus.current = false;
      // on a phone the panel is a full-screen sheet over the centre: the chat must be SEEN, so the
      // sheet steps aside (it keeps its place, and comes back as it was)
      if (pagesFullRef.current) setPagesOpenRef.current(false);
      return;
    }
    setPages(list);
    setListing(null);
    // CLOSED UNTIL SOMETHING IS HERE (founder 2026-10-15): a chat with nothing of its own opened
    // folds the panel; one with tabs (or its meeting) keeps the reader's own open/closed choice.
    if (!panelShows(list.length, false)) setPagesOpenRef.current(false);
    else if (featureOn("home2") && list.length && list.every((pg) => pg.path === ".workspace")) setPagesOpenRef.current(true);
    // a notice answers a link from the chat that was in front before; it does not follow a switch
    setNotice(null);
    if (front) { setDocPath(front.path); setDocSlug(front.slug); setDocKind(front.kind === "meeting" ? "meeting" : "doc"); }
    // …and a chat with nothing open shows nothing of the LAST chat's: its own artifact list instead.
    else { setDocPath(""); setDocSlug(undefined); setDocKind("doc"); }
    setHist(front ? { stack: [{ kind: front.kind, path: front.path, slug: front.slug, label: front.label }], i: 0 } : { stack: [], i: -1 });
    setDocBody(null); setDocNonce((n) => n + 1);
    // A new conversation is a fresh desk: nothing in it has been chosen by the reader yet, so an
    // artifact this chat writes may take the front (F41).
    readerChoseFocus.current = false;
  }, [meetings, mountSet, pendingView]);

  /** Open a rail row. A row derived from a meeting has no chat yet — first open MATERIALISES one
   *  (id `meet-<meetingId>`, so it lands on the agent session that meeting has always used).
   *  Returns the id of the chat that was actually opened: a caller with something to say to it
   *  (a proposal chip's kick) must address the chat that LANDED, never a reconstructed id. */
  const openRow = useCallback(async (r: Row, opts: { touched?: boolean; artifacts?: Artifact[]; focus?: string; front?: "meeting" } = {}) => {
    const existing = r.chatId ? chatsRef.current.find((c) => c.id === r.chatId) : undefined;
    const c = existing ?? chatForRow(chatsRef.current, r, meetings);
    let want = opts.touched ? { ...c, touched: true } : c;
    // THE LINK SETS THE RECORD (PRD decision 18). A preset that declares `tabs:` is saying what its
    // conversation is ABOUT, so those artifacts go on the chat — and the panel then renders from
    // the record like it does for every other chat, rather than from a second, parallel notion of
    // "what the link wanted". Only on a chat that has none yet: a reader who has since opened or
    // closed tabs owns them, and a re-click must not tidy their desk out from under them.
    if (opts.artifacts?.length && !want.artifacts.length) {
      want = { ...want, artifacts: opts.artifacts, focus: opts.focus ?? want.focus };
    }
    if (!existing || opts.touched || opts.artifacts?.length) persist((prev) => upsertChat(prev, want));
    await openChat(want, { front: opts.front });
    return want.id;
  }, [meetings, openChat, persist]);

  const openMeeting = useCallback(async (m: MeetingMock, opts: { touched?: boolean; artifacts?: Artifact[]; focus?: string; front?: "meeting" } = {}) => {
    const id = String(m.id);
    const row = railRows(chatsRef.current, [m]).find((r) => r.meetingId === id);
    return row ? await openRow(row, opts) : null;
  }, [openRow]);

  const addChat = useCallback((label: string, workspaces: string[], opts: { id?: string; kick?: string; say?: string; meeting?: string; artifacts?: Artifact[]; focus?: string; scaffold?: { kind: string; id: string }; scaffoldId?: string } = {}) => {
    const base = newChat(label, workspaces, { id: opts.id, touched: true, meeting: opts.meeting, scaffold: opts.scaffold });
    // A preset's declared tabs ARE the chat's opening artifacts — see openRow. A chat born from a
    // link is the one case where the record is written before a human has touched the panel.
    const c = opts.artifacts?.length ? { ...base, artifacts: opts.artifacts, focus: opts.focus } : base;
    persist((prev) => upsertChat(prev, c));
    void openChat(c);
    if (opts.kick) fireKick(c.id, opts.kick, opts.say, opts.scaffoldId);
    return c;
  }, [openChat, persist]);
  const addChatRef = useRef<typeof addChat | null>(null);
  addChatRef.current = addChat;

  /** `+` — open a chat that IS NOT A RECORD YET (F35). Nothing is written; the rail gains no row
   *  (the draft is not in `allChats`); the panel opens on the room's own pages exactly as it would
   *  for a stored chat. It becomes real at the first human turn and not one moment earlier. */
  const startDraft = useCallback((at?: string) => {
    // STARTED FROM INSIDE A WORKSPACE, IT WRITES THERE (UI decision 6b): the page in front names it —
    // or the caller does (`at`; "" is the person's own desk).
    const inside = at !== undefined ? insideWorkspace(at) : insideWorkspace(docSlugRef.current);
    const c = newChat("New chat", inside ? ["personal", "_global", inside] : ["personal", "_global"],
                      { touched: false, ...(inside ? { target: inside } : {}) });
    setDraft(c);
    void openChat(c);
    if (inside) void setChatTarget(c.id, inside).then(() => notifyScope()).catch(() => undefined);
    return c;
  }, [openChat]);
  const startDraftRef = useRef(startDraft);
  startDraftRef.current = startDraft;

  /** HOME (JTBD-1, home2): a fresh, unwritten chat, whose empty view IS the home page (workspace
   *  cards, "+ New workspace"), in front and never aimed at a workspace. */
  const goHome = useCallback(() => {
    const c = newChat("New chat", ["personal", "_global"], { touched: false });
    setDraft(c);
    void openChat(c);
    return c;
  }, [openChat]);

  // OPENING THE APP LANDS ON HOME (JTBD-1): once home2 is known, the restored last chat gives way
  // to the home page, unless the address named something or the person already moved.
  const landedHome = useRef(false);
  const bootChat = useRef(sel.chatId);
  useEffect(() => {
    if (!home2 || landedHome.current) return;
    landedHome.current = true;
    if (selRef.current.chatId !== bootChat.current) return;
    if (draftRef.current && draftRef.current.id === bootChat.current) return;
    let where = { pathname: "/", search: "", hash: "" };
    try { where = { pathname: window.location.pathname, search: window.location.search, hash: window.location.hash }; } catch { /* no window */ }
    if (landsOnHome({ home2, ...where, deepLinked: bootDeepLinked || !!wsLink })) goHome();
  }, [home2, goHome, bootDeepLinked, wsLink]);

  // "START A NEW CHAT AT LEVEL N" (hosted plans, `levels`): a new chat, its level set before its
  // first message, so the level is chosen at the start as the prompt cache needs.
  useEffect(() => {
    const on = (e: Event) => {
      const level = Number((e as CustomEvent<{ level?: number }>).detail?.level);
      if (!(level >= 1 && level <= 5)) return;
      const c = startDraft();
      void fetch("/api/chat/level", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ session: c.id, level, switch: true }) })
        .then((r) => { if (r.ok) window.dispatchEvent(new CustomEvent(LEVEL_SET_EVENT, { detail: { session: c.id, level } })); })
        .catch(() => undefined);
    };
    window.addEventListener(NEW_CHAT_AT_LEVEL_EVENT, on);
    return () => window.removeEventListener(NEW_CHAT_AT_LEVEL_EVENT, on);
  }, [startDraft]);

  /** The draft's ONE promotion path: a human turn happened in it, so it becomes a record — named by
   *  that same turn (F38), in one write rather than a record now and a name afterwards. */
  const promoteDraft = useCallback((d: ChatRec, text: string) => {
    persist((prev) => upsertChat(prev, { ...nameChat(d, text), touched: true, lastActivityAt: Date.now() }));
    setDraft((cur) => (cur && cur.id === d.id ? null : cur));
  }, [persist]);

  // LEAVING AN UNTOUCHED DRAFT LEAVES NOTHING BEHIND. There is no record to remove and no cleanup
  // pass to run — dropping the component state that held it IS the whole of "it never existed".
  useEffect(() => {
    setDraft((d) => (d && d.id !== sel.chatId ? null : d));
  }, [sel.chatId]);

  // ── THE RAIL IS THE SERVER'S SESSIONS (Vexa-ai/vexa#1591, founder walk 2026-09-06) ───────────
  //
  //  He signed in again in a new window after a morning of work on this instance and got an empty
  //  rail: *"i logged in again and now see no chats and it's starting over again while it has the
  //  context"*. `vexa.minutes.chats` is ONE browser's storage — a second window, a second machine or
  //  cleared site data showed nothing, while every one of those conversations was on the server.
  //
  //  So the list is fetched and MERGED (`mergeChats` owns the field-by-field rules). Local caches;
  //  the server owns. It runs after the owner check above — same `email` dependency, declared
  //  second, so a rail being dropped for the wrong identity is never the thing we merge into.
  //
  //  A FAILED FETCH COSTS NOTHING. The stored rail is what the reader already had, and a rail that
  //  emptied itself because one request failed would be the reported defect with a new cause.
  const landed = useRef(false);
  // `openChat` is rebuilt whenever the meetings list polls, and this must not become a fetch per
  // poll — the rail is read ONCE, when the identity arrives, so the callback rides a ref instead of
  // the dependency array.
  const openChatRef = useRef(openChat);
  useEffect(() => { openChatRef.current = openChat; }, [openChat]);
  // A TURN FROM ANOTHER AGENT LINKS THE THREAD IT CAME FROM (ADR-0046 decision 13). Opened only
  // when it is one of this reader's own chats; another person's thread is named, never opened.
  useEffect(() => {
    const open = (e: Event) => {
      const id = String((e as CustomEvent).detail?.chat ?? "");
      const c = chatsRef.current.find((x) => x.id === id);
      if (c) void openChatRef.current(c);
    };
    window.addEventListener(OPEN_CHAT_EVENT, open);
    return () => window.removeEventListener(OPEN_CHAT_EVENT, open);
  }, []);
  useEffect(() => {
    if (!email) return;
    let live = true;
    void listSessions().then((rows) => {
      if (!live) return;
      const fromServer = chatsFromSessions(rows);
      const hidden = loadHidden();
      persist((prev) => dropEmptyUnlisted(mergeChats(prev, fromServer, hidden), fromServer));
      // The rail is now as complete as it is going to get — what a `/w/` link waits on before it
      // picks the chat to land in. Same tick as the merge, so the landing reads the merged list.
      setRailSettled(true);
      // The same pure merge over what is in hand, read-only, so the landing choice below is not an
      // assignment smuggled out of a state updater React may run twice.
      const newest = [...mergeChats(chatsRef.current, fromServer, hidden)]
        .sort((a, b) => (b.lastActivityAt || 0) - (a.lastActivityAt || 0))[0] ?? null;
      // …AND A RETURNING PERSON LANDS ON THEIR CHATS, not on an empty composer beside them. Only
      // from the UNTOUCHED DRAFT the shell opens on when it knew of no chats — a draft is always
      // what is in front when it exists (the effect above clears it the moment the selection
      // moves), so this can never take a conversation away from under somebody. A deeplink or a
      // `?s=` arrival chose the room already and always wins; once, so a later re-render or a
      // second fetch cannot yank the reader out of what they have since opened.
      if (landed.current) return;
      landed.current = true;
      // …and only from the draft the shell OPENED ON: a new chat the person started with `+` before
      // the list arrived is their choice, and the landing must not take it away (lab 2026-10-14:
      // the new-chat home vanished behind the newest chat a second after `+`).
      if (deeplinkPending || pendingArrival() || !draftRef.current || draftRef.current.id !== firstOpen.id || !newest) return;
      setDraft(null);
      void openChatRef.current(newest);
    }).catch(() => { if (live) setRailSettled(true); });
    return () => { live = false; };
  }, [email, persist, deeplinkPending]);

  /** A person was added to a chat only its owner was in, so the server started a NEW shared chat
   *  (ADR-0048): read the rail, which now lists it, and open it. The private chat stays as it was. */
  const openSharedSession = useCallback((sid: string) => {
    void listSessions().then((rows) => {
      const fromServer = chatsFromSessions(rows);
      persist((prev) => mergeChats(prev, fromServer, loadHidden()));
      const c = fromServer.find((x) => x.id === sid);
      if (c) void openChatRef.current(c);
    }).catch(() => {});
  }, [persist]);
  // A CHAT JOINED OR LEFT THIS PERSON'S RAIL (#1783): somebody added them to it, or they left or
  // were removed, in this tab or another. The socket says so (`rail` frames); the rail reads its
  // chats and its folders again now, and a chat somebody else owns that is no longer listed goes.
  useEffect(() => {
    const onRail = (e: Event) => {
      const d = (e as CustomEvent<{ change?: string; session?: string }>).detail ?? {};
      void listSessions().then((rows) => {
        const fromServer = chatsFromSessions(rows);
        persist((prev) => dropEmptyUnlisted(dropLeftShared(mergeChats(prev, fromServer, loadHidden()), fromServer), fromServer));
        if (d.change === "removed" && d.session && d.session === sel.chatId && !fromServer.some((c) => c.id === d.session)) startDraft();
      }).catch(() => {});
      void refreshFolders();
    };
    window.addEventListener(RAIL_CHANGED_EVENT, onRail);
    return () => window.removeEventListener(RAIL_CHANGED_EVENT, onRail);
  }, [persist, refreshFolders, sel.chatId, startDraft]);
  // the rail's live refresh rides the user socket whether or not notifications are switched on
  useEffect(() => startRailFrames(), []);
  // A `/r/chat/<id>` link landed here (#1783, `app/r/refRoute.ts`): open that chat, once.
  useEffect(() => {
    let sid: string | null = null;
    try { sid = localStorage.getItem("vexa.openSession"); localStorage.removeItem("vexa.openSession"); } catch { /* none */ }
    if (sid) openSharedSession(sid);
  }, [openSharedSession]);
  // the composer shared a private chat by @-mention: the message goes to the new chat, so open it
  useEffect(() => {
    const onOpen = (e: Event) => {
      const sid = (e as CustomEvent<{ session?: string }>).detail?.session;
      if (sid) openSharedSession(sid);
    };
    window.addEventListener(OPEN_SESSION_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_SESSION_EVENT, onOpen);
  }, [openSharedSession]);
  // THE AGENT CALLED SOMEONE FOR THIS PERSON ("Call tester-b", call-person-autojoin): open the room
  // and join as the caller at once, shown as their outgoing call with Cancel. The chat raises this
  // only in the view that placed the call; `joinPlacedCall` re-reads the call and acts only when the
  // viewer owns it. meeting-api ends a call nobody joins within 30 s — this is what keeps it alive.
  useEffect(() => {
    const onPlaced = (e: Event) => {
      void joinPlacedCall((e as CustomEvent).detail, { ...livePlacedDeps, open: openSharedSession });
    };
    window.addEventListener(CALL_PLACED_EVENT, onPlaced);
    return () => window.removeEventListener(CALL_PLACED_EVENT, onPlaced);
  }, [openSharedSession]);

  // Refresh server-authored names without disturbing selection or reader-owned pages.
  useEffect(() => {
    if (!email) return;
    let live = true;
    const timer = setInterval(() => { void listSessions().then(data=>{
      if(!live)return;
      const named = chatsFromSessions(data);
      persist(prev=>dropEmptyUnlisted(dropLeftShared(mergeChats(prev,named,loadHidden()),named),named));
      const current = named.find(c=>c.id===sel.chatId && c.nameSource);
      if(current)setSel(x=>x.chatId===current.id?{...x,label:current.label}:x);
    }).catch(()=>{}); }, 5000);
    return ()=>{live=false;clearInterval(timer);};
  }, [email, sel.chatId, persist]);

  /** Fire a proposal chip. The founder chose IMMEDIATE, for consistency with the emailed links:
   *  a click sends the turn, with nothing left in the composer to press Enter on. And it sends it
   *  HERE — **a chip acts in the chat it renders in and never mints a row** (founder, 2026-09-01:
   *  he pressed one inside a chat he had just created and got a second one — "clicking this button
   *  should not create a new chat, this chat is already new").
   *
   *  What the click does to the record is `applyProposal`, which is pure and tested; this is the
   *  wiring only — persist it, rename the chat in front if nobody named it, say the line into the
   *  SAME session. A connect chip opens the Connections panel instead and says nothing. */
  const runProposal = async (p: Proposal) => {
    // …including inside a DRAFT: the chat in front is the draft when there is one, so a chip
    // PROMOTES it rather than minting a second row beside it. That is the founder's 2026-09-01
    // ruling ("this chat is already new") applied to F35's unwritten chat.
    const current = chatsRef.current.find((c) => c.id === sel.chatId) ?? draftRef.current ?? null;
    const eff = applyProposal(p, current);
    // Opening Connections says nothing and touches no chat, so it does not spend the offer.
    if (eff.act === "connect") {
      window.dispatchEvent(new CustomEvent(CONNECTIONS_OPEN, { detail: { provider: eff.provider, connection_id: eff.connectionId } }));
      return;
    }
    // …and so does a call with Vexa: it is a chat of its own.
    if (eff.act === "call") { startVexaCall(); return; }
    // A JOB LEAVES THE LIST WHEN ITS ACT RUNS (#1614). Locally first, so the row cannot re-offer it
    // while the close is in flight, and the close itself is fire-and-forget: a failed one costs a
    // chip that comes back on the next load, never a click that did nothing.
    if (p.itemId) {
      setDeskItems((prev) => prev.filter((i) => i.id !== p.itemId));
      void resolveProposal(p.itemId, "ran");
    }
    // The offer is spent the moment it is taken. A kick is hidden AND settle-delayed, so without
    // this the row would sit there for another 1.2 seconds, live, offering to do the same thing.
    setSpent(sel.chatId);
    if (eff.act === "create") { addChat(eff.label, ["personal", "_global"], { kick: eff.kick, say: eff.say }); return; }
    // The chat in front, mutated in place: touched and named, never re-roomed.
    persist((prev) => upsertChat(prev, eff.chat));
    setDraft((d) => (d && d.id === eff.chat.id ? null : d));
    setSel((x) => ({ ...x, label: eff.chat.label }));
    if (eff.kick) fireKick(eff.chat.id, eff.kick, eff.say);
  };

  /** Up to four chips, recomputed from the person's connections, their next calendar meeting, the
   *  desk's own facts and its short list. Pure, so the row is decided in the render that draws it —
   *  no model call and no effect. */
  const chips = useMemo(
    () => proposals(connections, events, Date.now(), desk, email, deskItems, chats.map((c) => c.label)),
    [connections, events, desk, email, deskItems, chats]);
  /** THE PERSON SAYING NO. It closes the row on the server and drops it here, and — unlike a pick —
   *  it does NOT spend the offer: the rest of the list is still worth having. */
  const dismissProposal = (p: Proposal) => {
    if (!p.itemId) return;
    setDeskItems((prev) => prev.filter((i) => i.id !== p.itemId));
    void resolveProposal(p.itemId, "dismissed");
  };
  /** THE STANDING ROW IS GONE (Vexa-ai/vexa#1600). #1586 put "Open transcript" · "Open note" above
   *  the composer of every meeting chat, because `×` on the transcript tab had left the founder with
   *  no way back to it. Shown that row, he ruled on the cause instead: *"just keep a tab that can't
   *  be closed instead"*. A chip is a way back from a mistake the product allowed; a tab with no `×`
   *  is the mistake not being available — so `openChips`, `OpenChips` and the composer slot they
   *  stood in are deleted rather than left unreachable, and `Page.permanent` is what replaces them. */
  const selMeeting = sel.kind === "meeting" ? meetings.find((m) => String(m.id) === sel.meetingId) : undefined;
  /** Which chat has already spent its offer — see `runProposal`. Per chat, because a different
   *  conversation has not been offered anything yet. */
  const [spent, setSpent] = useState<string | null>(null);
  const shownChips = homeChips(startersFor({ chat: sel.chatId, draft: draft?.id, spent, chips }), home2);

  // ── THE NEW-CHAT HOME (#1783) ─────────────────────────────────────────────────────────────
  // One ranked stream the background `home_compose` flow wrote. A STORE READ — never a model call —
  // re-read when a different chat comes to the front and when the connections answer (their ready
  // providers are evidence for the onboarding steps). Absent (no store, a failed read), the empty
  // chat keeps the chips it always had.
  const [homeRead, setHomeRead] = useState<HomeRead | null>(null);
  const readyProviders = Array.isArray(connections) ? connections.filter((c) => c.status === "ready").map((c) => c.provider).sort().join(",") : "";
  useEffect(() => {
    let live = true;
    const tz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; } })();
    void readHome(readyProviders ? readyProviders.split(",") : [], tz).then((r) => { if (live && r) setHomeRead(r); });
    return () => { live = false; };
  }, [sel.chatId, readyProviders]);
  /** A home button: its prompt, fired into THIS chat exactly as a chip fires (`runProposal`) — the
   *  new chat in front is the "new chat" the act goes into. The click is feedback, and so is the
   *  chat it landed in. */
  /** THINK OUT LOUD (founder, 2026-10-14): a new chat with a one-person call in it — the call
   *  becomes a page with its transcript. It replaces the voice note everywhere it was offered. */
  // AN @-PICK INVITED SOMEONE TO A CALL (founder: "@ is to tag and to invite to a call"): this chat's
  // call starts, or is joined; everyone in the chat is rung by the server (`call_started`).
  useEffect(() => {
    const on = (e: Event) => {
      const sid = String((e as CustomEvent<{ session?: string }>).detail?.session ?? "");
      if (!sid) return;
      const label = chatsRef.current.find((c) => c.id === sid)?.label ?? "Call";
      void enterCall(sid, label).catch(() => { /* the dock says why the call did not start */ });
    };
    window.addEventListener(CALL_START_EVENT, on);
    return () => window.removeEventListener(CALL_START_EVENT, on);
  }, []);
  const startVexaCall = (card?: Card | null) => {
    if (card) sendFeedback(card, "click", { title: cardTitle(card) });
    const c = addChat("Thinking out loud", ["personal", "_global"]);
    void enterCall(c.id, c.label).catch(() => { /* the dock says why the call did not start */ });
  };
  // START A MEETING (shell2, the Meetings page, UI 7w): a chat of its own with a call in it, in My
  // desk — "a meeting can be just a meeting", filed later. The list closes: the call is in front.
  const startMeetingRef = useRef<() => void>(() => undefined);
  startMeetingRef.current = () => {
    closeCentre();
    const c = addChat("Meeting", ["personal", "_global"]);
    void enterCall(c.id, c.label).catch(() => { /* the dock says why the call did not start */ });
  };
  useEffect(() => {
    const on = () => startMeetingRef.current();
    window.addEventListener(START_MEETING_EVENT, on);
    return () => window.removeEventListener(START_MEETING_EVENT, on);
  }, []);
  const runHomePrompt = (card: Card, prompt: string, label: string) => {
    const p: Proposal = { id: `home-${card.id}`, kind: "home", label, kick: homePrompt(prompt, card.id), say: prompt, title: cardTitle(card) };
    sendFeedback(card, "click", { people: cardPeople(card), title: cardTitle(card) });
    // A HOME ACT OPENS A NEW CHAT (founder, UI lab 2026-10-14): only the new chat in front is the
    // place it goes; from any other chat it starts one, never posting into a routine's or a
    // meeting's thread that happened to be open.
    if (draft?.id !== sel.chatId) {
      addChat(label, ["personal", "_global"], { kick: p.kick, say: prompt });
      return;
    }
    sendFeedback(card, "sent", { chat: sel.chatId, title: cardTitle(card) });
    void runProposal(p);
  };
  /** MESSAGE A PERSON. On Vexa: the one 1:1 shared chat the two of you have (the same id from either
   *  side), created on first use, opened with the composer focused — the agent stays quiet there
   *  unless @mentioned (policy `session_agent_speaks`). External: a new agent chat addressed to them
   *  that only drafts; sending is the person's explicit go on the final text. */
  const messagePerson = async (person: Person, card: Card | null) => {
    if (card) sendFeedback(card, "click", { people: [person.key], title: cardTitle(card) });
    const you = homeRead?.you ?? "";
    if (person.vexa && person.account_id && you) {
      const sid = directChatId(you, person.account_id);
      const mine = chatsRef.current.find((c) => c.id === sid);
      const focus = () => window.setTimeout(() => window.dispatchEvent(new Event("vexa:terminal:focus-chat")), 50);
      if (mine) { await openChat(mine); focus(); return; }
      try {
        const v = await listParticipants(sid).catch(() => null);
        if (!v || !v.participants.some((x) => x.principal === person.account_id)) {
          await addParticipant(sid, { subject: person.account_id, history: "all" });
        }
        openSharedSession(sid);
        focus();
        return;
      } catch { /* fall through to an addressed agent chat */ }
    }
    addChat(`Message to ${person.name}`, ["personal", "_global"], { kick: externalMessagePrompt(person), say: `Draft a message to ${person.name}` });
  };
  // A LIVE DASHBOARD (founder, 2026-10-13): a card moves in place when its work moves (`home.item`
  // over the socket), and a new composition is read only when the pointer is off the home, so
  // nothing reorders under the cursor.
  const readyRef = useRef(readyProviders);
  readyRef.current = readyProviders;
  useEffect(() => {
    let pending = false;
    const reread = () => {
      pending = false;
      const tz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; } })();
      void readHome(readyRef.current ? readyRef.current.split(",") : [], tz).then((r) => { if (r) setHomeRead(r); });
    };
    const onLeave = () => { if (pending) reread(); };
    const onFrame = (e: Event) => {
      const f = (e as CustomEvent).detail as { type: string; item?: string; state?: string; chat?: string; page?: string };
      if (f.type === "home.item" && f.item && f.state) {
        setHomeRead((r) => (r ? { ...r, doc: patchHome(r.doc, { item: f.item!, state: f.state!, chat: f.chat, page: f.page }, Date.now()) } : r));
        return;
      }
      if (f.type !== "home.composed") return;
      const el = document.querySelector("[data-home]");
      if (el && el.matches(":hover")) {
        pending = true;
        el.addEventListener("mouseleave", onLeave, { once: true });
      } else reread();
    };
    window.addEventListener(HOME_FRAME_EVENT, onFrame);
    return () => window.removeEventListener(HOME_FRAME_EVENT, onFrame);
  }, []);
  /** ONE SET OF BEHAVIOURS for every block on screen — the home, a page's ```vexa fence, a reply. */
  const homeHandlers: BlocksHandlers = {
    onAction: (card, a) => {
      if (a.call) { startVexaCall(card); return; }
      if (a.open?.chat) {
        if (card) sendFeedback(card, "click", { chat: a.open.chat, title: cardTitle(card) });
        const c = chatsRef.current.find((x) => x.id === a.open?.chat);
        if (c) void openChat(c); else openSharedSession(a.open.chat);
        return;
      }
      if (a.prompt && card) { runHomePrompt(card, a.prompt, a.label); return; }
      if (a.prompt) void runProposal({ id: `blocks-${a.label}`, kind: "home", label: a.label, kick: a.prompt, say: a.prompt, title: a.label });
    },
    onMessage: (person, card) => { void messagePerson(person, card); },
    onOpenPerson: (person) => { void runProposal({ id: `person-${person.key}`, kind: "home", label: person.name, kick: personPagePrompt(person), say: personPagePrompt(person), title: person.name }); },
    onDismiss: (card, reason) => sendFeedback(card, "dismiss", { reason, people: cardPeople(card), title: cardTitle(card) }),
    onSnooze: (card) => sendFeedback(card, "snooze", { people: cardPeople(card), title: cardTitle(card) }),
    onExpand: (card) => expandCard(card),
    onPeekPage: async (path, slug) => {
      const i = slug === undefined ? path.indexOf("/") : -1;
      const where = slug ? { slug } : i > 0 ? { slug: path.slice(0, i) } : undefined;
      const text = await readWorkspaceFile(i > 0 ? path.slice(i + 1) : path, where).catch(() => "");
      const p = peekFromMarkdown(String(text ?? ""));
      return { summary: p.summary, properties: p.properties, blocks: p.fence?.blocks, people: p.fence?.people };
    },
    onOpenPage: (path) => {
      const i = path.indexOf("/");
      if (i <= 0) return;
      readerChoseFocus.current = true;
      openPage({ path: path.slice(i + 1), slug: path.slice(0, i), label: (path.split("/").pop() || path).replace(/\.md$/i, "") } as Page, true);
    },
  };


  // Deleting a chat drops it from the rail (its agent session stays on the server — the row is the
  // user's index, not the record). A meeting's row comes back as a derived row, because the meeting
  // itself did not go anywhere.
  const renameChat = async (title: string) => {
    const id = sel.chatId;
    if (draftRef.current?.id !== id) {
      const response = await fetch("/api/chat/name", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({session:id,title})});
      if(!response.ok) throw new Error("Rename failed");
    }
    persist(prev=>prev.map(c=>c.id===id?{...c,label:title,nameSource:"human"}:c));
    setDraft(d=>d?.id===id?{...d,label:title,nameSource:"human"}:d);
    setSel(x=>x.chatId===id?{...x,label:title}:x);
  };
  const deleteChat = async (chatId: string, opts?: { retireRoutine?: boolean }) => {
    // A ROUTINE'S THREAD ASKS FIRST (ADR-0046): the rail calls this with the answer. Retiring is the
    // routine's own verb; deleting the chat alone leaves the routine running.
    const routine = chatsRef.current.find((c) => c.id === chatId)?.routine;
    if (routine && opts?.retireRoutine) void retireRoutine(routine.flow).catch(() => undefined);
    // DELETE IS THE SERVER'S NOW. It used to drop the stored row and write a tombstone in this
    // browser, which left the chat on the server and on every other device — and a meeting's own
    // chat came straight back as the meeting's derived row, under the same name: "delete chat is not
    // working". The server removes the session and its conversation, and hides the meeting's row for
    // this person (`hidden_meeting`). A 404 is a chat that never reached the server: removing the
    // stored row is the whole delete.
    setOrderError("");
    let res: Response;
    try { res = await fetch(`/api/sessions/${encodeURIComponent(chatId)}`, { method: "DELETE" }); }
    catch { setOrderError("Couldn't delete the chat. Try again."); return; }
    if (!res.ok && res.status !== 404) {
      setOrderError(res.status === 409 ? "This chat still has queued messages. Try again when they have run." : "Couldn't delete the chat. Try again.");
      return;
    }
    persist((prev) => removeChat(prev, chatId));
    await refreshFolders(chatOrder);
    if (sel.chatId !== chatId) return;
    // There is no home row to fall back to any more (F34 deleted it), so the shell lands on the
    // most recent chat that still exists — or, when none does, on a fresh draft: a composer with
    // nothing written anywhere.
    const next = chatsRef.current.filter((c) => c.id !== chatId)
      .sort((a, b) => (b.lastActivityAt || 0) - (a.lastActivityAt || 0))[0];
    if (next) void openChat(next); else startDraft();
  };

  // The tab strip IS the chat's `artifacts[]`, and this effect is its ONE writer. Persisting here
  // rather than at each call site is what makes that true: `openChat` commits `sel.chatId` and
  // `pages` together, so this can never write one chat's tabs onto another. A mock chat is not in
  // the stored list, so it simply finds no row and writes nothing.
  useEffect(() => {
    if (!docPath) return;
    const id = sel.chatId;
    const focus = artifactKey({ kind: docKind, path: docPath, slug: docSlug });
    // The VIEW is reading state exactly as the tabs are, so it persists with them — reopening a
    // chat puts back the document you were looking at whether or not you pinned it. Before
    // decision 28 the only way the panel could remember a document was to make it a tab, which is
    // precisely how the pile accumulated. NOTE the guard moved from `pages.length` to `docPath`:
    // a chat with NO tabs still has a view worth remembering.
    const label = (docPath.split("/").pop() || docPath).replace(/\.md$/i, "");
    const view: Artifact = { kind: docKind === "meeting" ? "meeting" : undefined, path: docPath, slug: docSlug, label };
    persist((prev) => {
      const i = prev.findIndex((c) => c.id === id);
      if (i < 0) return prev;
      const c = prev[i];
      // The comparison has to include `pinned`/`desk`/`at`, not just identity and label: a plain
      // navigation between two pages the strip already holds changes ONLY `at`, and that is exactly
      // the change worth persisting — it is the strip's order.
      const same = c.focus === focus && c.artifacts.length === pages.length
        && artifactKey(c.view ?? { path: "" }) === artifactKey(view)
        && c.artifacts.every((a, k) => artifactKey(a) === artifactKey(pages[k]) && a.label === pages[k].label
          && !!a.pinned === !!pages[k].pinned && !!a.desk === !!pages[k].desk
          // `permanent` too, and it is not decoration: a chat that BINDS a meeting mid-conversation
          // gains it on tabs it already had (Vexa-ai/vexa#1600 via `withMeetingPages`), changing
          // nothing else — so without this the record would keep the closable copy.
          && !!a.permanent === !!pages[k].permanent && a.at === pages[k].at);
      if (same) return prev;
      const next = [...prev];
      // THE STRIP IS COPIED, NOT RE-DECIDED (decision 28). This mapped every entry to
      // `pinned: true` and dropped `at` and `desk` — three fields, and together they were the whole
      // model: nothing could age out (the cap only evicts UNPINNED), the order was lost on reload
      // (`orderHistory` sorts on `at`), and the home tier stopped being one. A writer's job here is
      // to persist what the strip IS; deciding pinned-ness on the way past is how one literal
      // nullified 28, 28.4 and 28.5 at once.
      next[i] = { ...c, artifacts: stripForRecord(pages), focus, view };
      return next;
    });
  }, [sel.chatId, pages, docPath, docSlug, docKind, persist]);

  // The agent should see what the human is reading. chat.tsx builds its context bundle from the
  // layout store's active tab (chatContext.focusTarget maps a `doc` tab to `{kind:"file", ref:
  // "@file:<path>"}`), and in minutes mode nothing ever set it — so `focus` went out null on every
  // turn while a document sat open beside the conversation. The workbench never mounts beside this
  // shell, so the store keeps exactly one writer. Only `path` reaches the wire today; `tabs` rides
  // along for the server to pick up when it wants the whole open set.
  useEffect(() => {
    // a meeting tab reaches the agent as a MEETING focus (chatContext.focusTarget maps it to
    // `{kind:"meeting"}`), never as a file path that does not exist.
    layout.setActiveTab(docKind === "meeting"
      ? { kind: "meeting", params: { meetingId: docPath } }
      : { kind: "doc", params: { path: docPath, slug: docSlug, tabs: pages.map((pg) => pg.path) } });
  }, [layout, docPath, docSlug, docKind, pages]);
  useEffect(() => () => layout.setActiveTab(null), [layout]);

  // Lay out the chat `firstOpen` already selected — the selection is decided in the first render,
  // this is only the panel catching up (it needs an await on the mount set). A pending deeplink
  // owns the first room, so this yields to one.
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current || deeplinkPending) return;
    booted.current = true;
    void openChat(firstOpen);
  }, [deeplinkPending, openChat, firstOpen]);

  // A user-authored send is the whole definition of "touched" — the cheap flag the default filter
  // reads instead of fetching every chat's history. chat.tsx fires this with its session id, which
  // IS the chat id.
  useEffect(() => {
    const onTouched = (e: Event) => {
      const d = (e as CustomEvent<{ session?: string; text?: string }>).detail;
      const id = d?.session;
      if (!id) return;
      const text = d?.text ?? "";
      const draftNow = draftRef.current;
      const before = draftNow?.id === id ? draftNow : chatsRef.current.find((c) => c.id === id);
      // A DRAFT's first human turn is the moment it becomes a record — F35's one write, carrying
      // F38's name. Until this fires the chat exists only in this component.
      if (draftNow && draftNow.id === id) promoteDraft(draftNow, text);
      // An already-stored chat still wearing a placeholder name takes its name from this turn too —
      // the naming rule is about the FIRST HUMAN TURN, not about how the record came to exist.
      else persist((prev) => nameFromTurn(markTouched(prev, id), id, text));
      // The header names the chat in front, so it takes the new name in the SAME beat the rail
      // does — `nameChat` is asked rather than re-implemented, so the three refusals (a scaffold's
      // own title, a meeting's title, a name a human chose) hold here too by construction.
      const after = before && nameChat(before, text);
      if (before && after && after.label !== before.label) {
        setSel((x) => (x.chatId === id ? { ...x, label: after.label } : x));
      }
    };
    window.addEventListener(CHAT_TOUCHED_EVENT, onTouched);
    return () => window.removeEventListener(CHAT_TOUCHED_EVENT, onTouched);
  }, [persist, promoteDraft]);

  /** Open a document as a TAB: already open → just focus it; new → append and focus. Every route
   *  into the panel goes through here (entity link, breadcrumb listing, phase page), which is why
   *  the tab set can be trusted as the record of what has been looked at. */
  /** HAS THE READER CHOSEN WHAT IS IN FRONT? Set by the panel's own tab clicks and by clicking a
   *  link in the conversation — a person's deliberate move — and read by the artifact listener,
   *  which appends but never moves them once they have. Decision 18's rule ("a second arrival must
   *  not tidy their desk out from under them") one level down: it is about a re-click there and
   *  about the agent's own writes here, and it is the same rule. */
  const readerChoseFocus = useRef(false);

  const openPage = useCallback((pg: Page, byReader = false) => {
    const e: Artifact = { kind: pg.kind, path: pg.path, slug: pg.slug, label: pg.label };
    // What this person actually opens is the desk README's ordering signal — and the only place
    // that knows it is here. Fire-and-forget; a usage signal is never worth a millisecond of the
    // document they asked for. (The seam worker's panel-view-slot lands the same one line.)
    if (pg.kind !== "meeting") reportOpened(pg.slug, pg.path);
    // A folded-away panel is the other way a link click "does nothing": the tab opens into a
    // column nobody can see. Asking for a document unfolds the column it lands in — and, where the
    // panel is a sheet, opens the sheet when the READER asked (a link they clicked), never for a
    // page the agent wrote while they were typing.
    revealPages.current(byReader);
    // standard history semantics: navigating after going BACK truncates the forward branch, and
    // re-opening the document already in front is not a navigation at all.
    setHist((h) => {
      const cur = h.stack[h.i];
      if (cur && artifactKey(cur) === artifactKey(e)) return h;
      const stack = [...h.stack.slice(0, h.i + 1), e];
      return { stack, i: stack.length - 1 };
    });
    // ONE VIEW SLOT, AND ONE PREVIEW TAB SHOWING IT (PRD decision 28; founder ruling 2026-09-06:
    // *"no need to create tabs, unless there is a pinned tab. Use obsidian rule for that"*).
    // Navigating replaces what the panel shows AND what stands in the preview slot: `touchHistory`
    // dedups by identity and evicts the page that was previewed, never a pin. A page worth keeping
    // is pinned — from the tab itself — and only then is it a tab.
    setPages((prev) => touchHistory(prev, { kind: pg.kind, path: pg.path, slug: pg.slug, label: pg.label }, Date.now()));
    // THE CHOKE POINT for a meeting id reaching the canvas: every route into the panel comes
    // through here, including a `meeting:<id>` artifact the agent wrote this turn. If the store has
    // never listed that row, ask for it now — the canvas is about to render it.
    if (pg.kind === "meeting") ensureMeetingKnown(pg.path);
    setDocPath(pg.path); setDocSlug(pg.slug); setDocKind(pg.kind === "meeting" ? "meeting" : "doc");
    setListing(null); setDocNonce((n) => n + 1);
    // A sentence about a link that would not open is answered by a document that did.
    setNotice(null);
  }, []);

  /** THE CHAT'S MEETING IN THE RIGHT PANEL (founder, 2026-10-14: "there is no visible meeting UI or
   *  transcript body in the panel"). The meeting view — transcript feed, Stop bot — is a panel tab:
   *  the header's live chip opens it, and a meeting that is happening opens it by itself, once per
   *  chat and meeting (`meetingToOpen`), so a reader who closes it keeps it closed. By itself it never
   *  pulls a phone's sheet over the conversation (`byReader` false); the chip does, because it was asked. */
  const openMeetingView = useCallback((m: MeetingMock, byReader: boolean) => {
    openPage({ kind: "meeting", path: String(m.id), label: (m as { title?: string }).title || "Meeting" } as Page, byReader);
  }, [openPage]);
  const autoOpenedMeetings = useRef(new Set<string>());
  const chatMeetingState = chatMeeting ? meetingStatusOf(chatMeeting) : "";
  useEffect(() => {
    const key = meetingToOpen(sel.chatId, chatMeeting, autoOpenedMeetings.current);
    if (!key || !chatMeeting) return;
    autoOpenedMeetings.current.add(key);
    openMeetingView(chatMeeting, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel.chatId, chatMeeting?.id, chatMeetingState]);

  /** PIN A PAGE. The amendment folded "open in tab" into this: the strip is history, so everything
   *  you open is already in it, and the only extra thing worth asking for is that one STAYS. */
  const openPinned = useCallback((pg: Page) => {
    void pinPage({ slug: pg.slug ?? null, path: pg.path, label: pg.label });
    openPage(pg, true);
    setPages((prev) => prev.map((x) => artifactKey(x) === artifactKey(pg) ? { ...x, pinned: true } : x));
  }, [openPage]);

  /** THE PIN IS ON THE TAB (founder ruling 2026-09-06: *"tab icon is on tab"*).
   *
   *  It used to be one control in the document header, about whatever was in front — so it could
   *  say nothing about the other tabs, and because it tested MEMBERSHIP of a strip that every
   *  navigation wrote to, it read "pinned" for every page the reader had merely walked past and its
   *  press deleted the entry rather than unpinning it. The decision belongs on the thing it is
   *  about: every tab carries its own, and `togglePinned` is the pure rule behind all of them. */
  const togglePin = useCallback((pg: Page) => {
    const front = artifactKey(pg) === artifactKey({ kind: docKind, path: docPath, slug: docSlug });
    // the person's list first (it is what every chat re-applies), then this chat's tabs
    if (pg.pinned) void unpinPage({ slug: pg.slug ?? null, path: pg.path });
    else if (pg.kind !== "meeting") void pinPage({ slug: pg.slug ?? null, path: pg.path, label: pg.label });
    setPages((prev) => togglePinned(prev, artifactKey(pg), front, Date.now()));
  }, [docKind, docPath, docSlug]);

  /** THE VIEW SLOT (decision 28) — the navigator moves what is IN FRONT and mints no tab. Reading a
   *  workspace by walking it is browsing, and browsing that collects leaves a strip nobody asked for.
   *
   *  THE LISTENER IS THE SEAM; `openPage` IS THE MECHANISM. It used to set the document state
   *  directly, which was correct while the view slot did not exist yet and wrong the moment it did:
   *  a navigator click skipped the back/forward stack and the strip's history, so it and a chip
   *  click for the same file landed in two different places. Routing it through `openPage` — the
   *  one route an entity chip, a wikilink and an `artifact` event already take — is what makes
   *  "the panel has one view slot" true of every way of reaching it rather than of most of them. */
  useEffect(() => {
    const onView = (e: Event) => {
      const d = (e as CustomEvent<ViewSlot>).detail;
      if (!d?.path) return;
      openPage({ path: d.path, slug: d.workspace, label: d.label }, true);
    };
    window.addEventListener(VIEW_NAVIGATE_EVENT, onView);
    return () => window.removeEventListener(VIEW_NAVIGATE_EVENT, onView);
  }, [openPage]);

  // ── `/w/<workspace>/<path>`: RESOLVE, THEN LAND (Vexa-ai/vexa#1643) ─────────────────────────
  //
  //  **The admin opened a shared workspace's README by URL and got a new chat and his own desk's
  //  README.** Two effects and one ordering rule replace that, and the rule is the fix: *the link
  //  lands ONCE, after the reader's own chats are known.* Nothing in the old arrival waited for
  //  anything, so the terminal's default — a draft chat, the desk's page — won every race it
  //  entered, and a URL that had been understood perfectly well produced no visible effect.
  //
  //  FIRST, ASK THE SERVER what the ref means FOR THIS READER: `readWorkspaceById` for a canonical
  //  id, `readWorkspaceBySlug` for the slug a person pastes. Nothing is rendered from the answer
  //  here — an answer that arrived before the chat list would open a page into a conversation that
  //  is about to be replaced.
  useEffect(() => {
    if (!wsLink) return;
    let live = true;
    void resolveWorkspaceDeepLink(wsLink, { byId: readWorkspaceById, bySlug: readWorkspaceBySlug })
      .then((out) => { if (live) setWsOutcome(out); });
    return () => { live = false; };
  }, [wsLink]);

  //  THEN LAND IT: the chat this link belongs in — never a new one when the reader has any — and
  //  then the page. `readerChoseFocus` is set for the same reason `open_page` sets it: a person
  //  asked for this page, so the next quiet write from a turn must not push it aside.
  //
  //  A REFUSAL LANDS TOO. The chat still opens (their conversations are still theirs) and the
  //  panel says the one sentence instead of a document — never the desk's README standing in for
  //  an answer, which is the half of #1643 that made the link look like a broken product.
  //
  //  The chat's MOUNT SET is deliberately untouched: reading a page somebody linked is not a
  //  statement about where this conversation works, and widening the set would quietly re-aim the
  //  next turn (Vexa-ai/vexa#1611). The page is readable because the reader is, which is the
  //  server's answer on `_read_target`, not something this client arranges by mounting.
  useEffect(() => {
    if (!wsLink || !wsOutcome || !railSettled || wsLanded.current) return;
    wsLanded.current = true;
    void (async () => {
      // The boot yielded to this link (`deeplinkPending`), so the chat in front has never actually
      // been laid out — which is why this opens it whether or not it is already the selection. The
      // draft is the last resort and only when there is genuinely nothing else: F35's draft is a
      // chat in the sense that you can type in it, and in no other, because it writes no record.
      const chosen = chatForDeepLink(chatsRef.current, wsOutcome.kind === "open" ? wsOutcome.workspace : undefined)
        ?? draftRef.current;
      if (chosen) await openChatRef.current(chosen);
      if (wsOutcome.kind === "refused") { setNotice(wsOutcome.sentence); return; }
      readerChoseFocus.current = true;
      // A LINK'S PAGE IS A TAB, not the preview slot: it is the reason this window is open, and
      // the reader's first click elsewhere would otherwise evict it (the Obsidian rule).
      openPinned(wsOutcome.page);
    })();
  }, [wsLink, wsOutcome, railSettled, openPinned]);

  /** Walk the stack without disturbing it. A document closed since it was visited is REOPENED as a
   *  tab — going back to somewhere you have been should never fail because you tidied up. */
  const step = (delta: number) => {
    const j = hist.i + delta;
    if (j < 0 || j >= hist.stack.length) return;
    const e = hist.stack[j];
    setHist({ ...hist, i: j });
    // …through the SAME rule a navigation uses: going back to a page you dropped puts it in the
    // preview slot. Appending it raw would stand a second unpinned tab beside the preview, which is
    // the accumulation the Obsidian ruling removed.
    setPages((prev) => touchHistory(prev, { kind: e.kind, path: e.path, slug: e.slug, label: e.label }, Date.now()));
    setDocPath(e.path); setDocSlug(e.slug); setDocKind(e.kind === "meeting" ? "meeting" : "doc");
    setListing(null); setDocNonce((n) => n + 1);
  };
  // A folder listing is not a document, so it is not in the stack — but it IS somewhere you went,
  // and the first ‹ should undo it rather than skipping past the doc you were reading.
  const canBack = !!listing || hist.i > 0;
  const canForward = !listing && hist.i >= 0 && hist.i < hist.stack.length - 1;
  const goBack = () => { if (listing) { setListing(null); return; } step(-1); };
  const goForward = () => step(1);

  /** Close a tab. The last one never closes — an empty panel is not a state worth reaching — and
   *  closing the tab in front hands focus to its neighbour rather than to nothing. */
  /** `×` FORGETS an entry. The strip is history, so this is not "close a tab" — it is the reader
   *  saying they do not want that page remembered. The last one may go too: an empty strip is a
   *  chat you have not read anything in, which is a real state and was reachable before this. */
  const closeTab = (pg: Page) => {
    const key = artifactKey(pg);
    const i = pages.findIndex((x) => artifactKey(x) === key);
    if (i < 0) return;
    const { next, unpin } = closingTab(pages, pg);
    if (unpin) void unpinPage({ slug: pg.slug ?? null, path: pg.path });
    setPages(next);
    if (key === artifactKey({ kind: docKind, path: docPath, slug: docSlug }) && next.length) {
      openPage(next[Math.min(i, next.length - 1)]);
    } else if (!next.length) {
      setDocPath(""); setDocSlug(undefined);   // the last tab closed: the panel folds away
      shell.setPagesOpen(false);
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
      if (e.key === "[") { e.preventDefault(); goBack(); }
      if (e.key === "]") { e.preventDefault(); goForward(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  /** The breadcrumb navigates. `listWorkspaceTree` returns every path in a workspace, so a folder
   *  is just that list cut at one prefix: the names with no further slash are files, the first
   *  segment of the rest are directories. No new endpoint, and no tree state to keep in step. */
  const navigate = useCallback(async (slug: string | undefined, prefix: string, name?: string) => {
    const files = await listWorkspaceTree(slug ? { slug } : undefined).catch(() => [] as string[]);
    const head = prefix ? `${prefix}/` : "";
    const dirs = new Set<string>(), leaves = new Set<string>();
    for (const f of files) {
      if (head && !f.startsWith(head)) continue;
      const rest = f.slice(head.length);
      if (!rest) continue;
      const cut = rest.indexOf("/");
      if (cut < 0) leaves.add(rest); else dirs.add(rest.slice(0, cut));
    }
    // the workspace's name rides along while the reader walks the same workspace
    setListing((prev) => ({ slug, prefix, dirs: [...dirs].sort(), files: [...leaves].sort(),
      name: name ?? (prev && prev.slug === slug ? prev.name : undefined) }));
  }, []);

  // A WORKSPACE'S FILES, ASKED FOR BY NAME (#1783): a desktop tile whose workspace has no README
  // shows the workspace's root here, where the listing offers Create README. Only the panel moves:
  // the chat in front is not mounted, retargeted or created.
  useEffect(() => {
    const onFiles = (e: Event) => {
      const d = (e as CustomEvent<{ workspace?: string; name?: string }>).detail || {};
      const slug = (d.workspace || "").trim() || undefined;
      void navigate(slug, "", (d.name || "").trim() || undefined).then(() => shell.setPagesOpen(true));
    };
    window.addEventListener(OPEN_WORKSPACE_FILES_EVENT, onFiles);
    return () => window.removeEventListener(OPEN_WORKSPACE_FILES_EVENT, onFiles);
  }, [navigate, shell]);

  /** The chat's focus set, edited from the header. The mount set follows immediately — the point of
   *  changing it is the next turn, not the next reload. */
  // Held in a ref for the focus-event effect below: `setWorkspaces` closes over `sel` and is rebuilt
  // every render, so subscribing to it directly would tear the listener down and up on each one.
  const setWorkspacesRef = useRef<(fn: (ws: string[]) => string[]) => void>(() => undefined);
  const setWorkspaces = (fn: (ws: string[]) => string[]) => {
    const id = sel.chatId, next = fn(sel.workspaces);
    setSel((x) => ({ ...x, workspaces: next }));
    persist((prev) => prev.map((c) => (c.id === id ? { ...c, workspaces: next } : c)));
    // A DRAFT is not in the stored list, so the mount set has to land on the record that IS in
    // front — otherwise it would be right for this sitting and lost the moment the draft promotes.
    setDraft((d) => (d && d.id === id ? { ...d, workspaces: next } : d));
    void mountSet(next);
  };
  setWorkspacesRef.current = setWorkspaces;

  /** WHERE THIS CHAT WRITES (Vexa-ai/vexa#1611) — the header chip's click, and the `focus` event's.
   *
   *  The ONE writer on this side, exactly as `setWorkspaces` is for the mount set, and it moves the
   *  same four things together: the selection (so the chip repaints now), the stored record, the
   *  draft that is not stored yet, and the SERVER — which is what makes it survive a reload and be
   *  true for the next turn's container, its cwd and its tool defaults.
   *
   *  `""` is the person's own desk. Best-effort on the wire, like `mountSet`: a chat whose target
   *  did not reach the index keeps writing where it was, which is the state it was already in. */
  const setChatTargetRef = useRef<(id: string, opts?: { justMounted?: boolean }) => void>(() => undefined);
  const chooseTarget = (wid: string, opts: { justMounted?: boolean } = {}) => {
    const id = sel.chatId, next = (wid || "").trim();
    // A target must be a workspace this chat is over — the same refusal `setTarget` makes on the
    // record and the server makes on the write. Refused HERE too so the chip cannot paint a state
    // the record will not keep.
    //
    // `justMounted` is the ONE caller that legitimately knows better: the `focus` handler adds the
    // mount and takes the target in one beat, and React has not committed the first half by the
    // time it asks for the second. Checking `sel` there would refuse the workspace we are looking
    // at — a guard failing on the case it was written to allow.
    if (next && !opts.justMounted && !sel.workspaces.includes(next)) return;
    setSel((x) => ({ ...x, target: next || undefined }));
    persist((prev) => setTarget(prev, id, next));
    setDraft((d) => (d && d.id === id ? { ...setTarget([d], id, next)[0] } : d));
    void setChatTarget(id, next).then(() => notifyScope()).catch(() => undefined);
  };
  setChatTargetRef.current = chooseTarget;
  // HOME2 (R3): the workspace page's "Expand" widens the pages panel (two columns), and back
  const pagesWidthBefore = useRef<number | null>(null);
  const wideRef = useRef<(wide: boolean) => void>(() => undefined);
  useEffect(() => {
    const on = (e: Event) => wideRef.current(!!(e as CustomEvent<{ wide?: boolean }>).detail?.wide);
    window.addEventListener(WORKSPACE_PAGE_WIDE_EVENT, on);
    return () => window.removeEventListener(WORKSPACE_PAGE_WIDE_EVENT, on);
  }, []);
  wideRef.current = (wide: boolean) => {
    const b = L.bounds.pages;
    if (!b) return;
    if (wide) { pagesWidthBefore.current ??= L.widths.pages; shell.commitWidth("pages", b.max); }
    else if (pagesWidthBefore.current != null) { shell.commitWidth("pages", pagesWidthBefore.current); pagesWidthBefore.current = null; }
  };
  // HOME2 (R3): a workspace status card on the home (or the workspace page's "New chat") aims the
  // home's new chat at that workspace — mounted first, then targeted, in one beat, through the one
  // writer above. "" is the person's own. Only `home2` cards send it, and never for a workspace they
  // can only read. ONLY THE DRAFT IS AIMED: a stored chat in front keeps where it writes (its own
  // chip and guard decide that), and the aim opens a new draft there instead.
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ workspace?: string; onlyHome?: boolean }>).detail ?? {};
      const wid = String(d.workspace ?? "").trim();
      if (homeTargetAct(selRef.current.chatId, draftRef.current?.id) === "new-draft") {
        // `onlyHome` (shell2, opening a workspace from the panel): a stored chat stays in front (7d)
        if (d.onlyHome) return;
        startDraftRef.current(wid); return;
      }
      if (wid) setWorkspacesRef.current((ws) => (ws.includes(wid) ? ws : [...ws, wid]));
      setChatTargetRef.current(wid, { justMounted: true });
    };
    window.addEventListener(HOME_TARGET_EVENT, on);
    return () => window.removeEventListener(HOME_TARGET_EVENT, on);
  }, []);
  // NEW CHAT ON A WORKSPACE'S PAGE (founder 2026-10-16: a dead click): always a fresh draft in the
  // centre, aimed there, composer focused, and prefilled when the page asked for words.
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ workspace?: string; draft?: string }>).detail ?? {};
      const wid = String(d.workspace ?? "").trim();
      const c = startDraftRef.current(wid === "personal" ? "" : wid);
      // shell2 (UI 7t): a workspace's own "New chat" is the ONE way into focus — the home collapses to
      // the input for that workspace; the target chip's × brings the full home back
      if (featureOn("shell2") && wid && wid !== "personal") { noteHomeTarget(wid); setHomeFocus(true); }
      window.setTimeout(() => {
        if (d.draft) window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: d.draft, mode: "draft" } }));
        window.dispatchEvent(new CustomEvent("vexa:terminal:focus-chat"));
      }, 80);
      return c;
    };
    window.addEventListener(NEW_CHAT_AT_EVENT, on);
    return () => window.removeEventListener(NEW_CHAT_AT_EVENT, on);
  }, []);
  // NEW ROOM in a workspace (founder 2026-10-16): the room dialog, aimed at it
  useEffect(() => {
    const on = (e: Event) => { const w = String((e as CustomEvent<{ workspace?: string }>).detail?.workspace ?? ""); setRoomDialogRef.current({ kind: "new", workspace: w || undefined }); };
    window.addEventListener(NEW_ROOM_EVENT, on);
    return () => window.removeEventListener(NEW_ROOM_EVENT, on);
  }, []);
  // THE PERSON'S SWITCH ON AN AGENT'S PROPOSAL (UI decision 6b): the same writer as the chip, for the
  // chat the proposal was made in — mounted first, then targeted, in one beat.
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ session?: string; workspace?: string }>).detail || {};
      if (!d.session || d.session !== selRef.current.chatId) return;
      const wid = String(d.workspace ?? "").trim();
      if (wid) setWorkspacesRef.current((ws) => (ws.includes(wid) ? ws : [...ws, wid]));
      setChatTargetRef.current(wid, { justMounted: true });
    };
    window.addEventListener(CHOOSE_TARGET_EVENT, on);
    return () => window.removeEventListener(CHOOSE_TARGET_EVENT, on);
  }, []);

  /** THE SCOPE LINE'S READS (Vexa-ai/vexa#1783) — the server's answer for this chat — kept on the
   *  record, so the pages panel opens what the chat actually reads. Stable, and a no-op when
   *  nothing changed, so a re-read never re-renders the shell for nothing. */
  const onScopeReads = useCallback((reads: string[], view?: ScopeView) => {
    const cur = selRef.current.workspaces;
    const next = [...reads, ...(cur.includes(GLOBAL_MOUNT) ? [GLOBAL_MOUNT] : [])];
    if (!(next.length === cur.length && next.every((w, i) => w === cur[i]))) setWorkspacesRef.current(() => next);
    // A TAB FOR A WORKSPACE THIS CHAT DOES NOT HAVE GOES (Vexa-ai/vexa#1783): the founder hid
    // Personal and still saw a pinned "Personal" tab.
    if (view) {
      const scope = { reads, target: view.chat.target, known: view.workspaces.map((w) => w.slug) };
      setPages((p) => dropStaleTabs(p, scope));
    }
  }, [setPages]);

  // ── THE DEFAULT FOR NEW CHATS (founder 2026-10-13) ───────────────────────────────────────────
  //  A person can make one workspace the place their new chats write to ("Make default for new
  //  chats" in the Writes to menu). agent-api keeps it per person and RE-CHECKS it on every read —
  //  removed, deleted, or lowered to reader, and the answer is Personal, unnamed — so it is asked
  //  again for each new draft rather than remembered here. It is applied through the same two
  //  writers a `focus` event uses (mount, then target), so the chip, the draft and the server move
  //  together. Only an untouched draft that has chosen nothing takes it: existing chats keep theirs.
  const [defaultTarget, setDefaultTargetState] = useState("");
  const draftId = draft?.id;
  useEffect(() => {
    const d = draftRef.current;
    if (!d || d.touched) return;
    let live = true;
    void getDefaultTarget().then((wid) => {
      if (!live) return;
      setDefaultTargetState(wid);
      const cur = draftRef.current;
      if (!cur || cur.id !== d.id || selRef.current.chatId !== d.id) return;
      if (withDefaultTarget(cur, wid) === cur) return;
      setWorkspacesRef.current((ws) => (ws.includes(wid) ? ws : [...ws, wid]));
      setChatTargetRef.current(wid, { justMounted: true });
    });
    return () => { live = false; };
  }, [draftId]);
  const makeDefaultTarget = (wid: string) => {
    void setDefaultTarget(wid).then((saved) => setDefaultTargetState(saved)).catch(() => undefined);
  };

  useEffect(() => {
    let dead = false;
    // a meeting tab has no body to fetch: the canvas fetches its own transcript by row id.
    if (docKind === "meeting") { setDocBody(null); return; }
    // a workspace's page (home2) is drawn from components, not read as a file
    if (isWorkspacePagePath(docPath)) { setDocBody(null); return; }
    // shell2: a person is drawn from the people read, not read as a file
    if (isPersonPath(docPath)) { setDocBody(null); return; }
    // mock bodies short-circuit the fetch entirely — no request is made for a fabricated page
    if (mock) {
      const canned = mockBody(docPath, Date.now() - mockStart.current);
      if (canned !== null) { setDocBody(canned); return; }
    }
    setDocBody(null);
    readWorkspaceFile(docPath, docSlug ? { slug: docSlug } : undefined)
      .then((c) => { if (!dead) setDocBody(c); })
      .catch(() => { if (!dead) setDocBody(null); });
    return () => { dead = true; };
  }, [docPath, docSlug, docKind, sel.chatId, docNonce, mock]);

  // A turn wrote to the workspace: re-read whatever is in front. This is what makes a tab a chat
  // declared before its file existed fill IN rather than sit on "no page here yet" — see
  // WORKSPACE_COMMIT_EVENT. Cheap: one read of the open document, only when a commit actually
  // landed, and the doc-link caches were already dropped by the same handler.
  useEffect(() => {
    const onCommit = () => setDocNonce((n) => n + 1);
    window.addEventListener(WORKSPACE_COMMIT_EVENT, onCommit);
    return () => window.removeEventListener(WORKSPACE_COMMIT_EVENT, onCommit);
  }, []);

  // A PERSON DELETED A PAGE (`./pageDelete`): every tab on it goes, and if it was in front its
  // neighbour comes forward — `closeTab`'s rule, applied to all of that page's tabs at once. No deps
  // on purpose, like the key listener above: it reads the strip as it is now.
  useEffect(() => {
    const onRemoved = (e: Event) => {
      const d = (e as CustomEvent<{ path?: string; slug?: string }>).detail;
      if (!d?.path) return;
      const next = dropRemovedPage(pages, d.path, d.slug);
      if (next.length === pages.length) return;
      const front = docKind !== "meeting" && docPath === d.path && (docSlug || undefined) === (d.slug || undefined);
      const i = pages.findIndex((x) => x.kind !== "meeting" && x.path === d.path && (x.slug || undefined) === (d.slug || undefined));
      setPages(next);
      if (front && next.length) openPage(next[Math.min(Math.max(i, 0), next.length - 1)]);
    };
    window.addEventListener(PAGE_REMOVED_EVENT, onRemoved);
    return () => window.removeEventListener(PAGE_REMOVED_EVENT, onRemoved);
  });

  // The live phase means words ARRIVING. Re-read the live transcript on a timer so "flowing" is
  // something you watch rather than something the label claims. Mock-only: the real live feed will
  // be a stream, not a poll.
  useEffect(() => {
    if (!mock || !docPath.endsWith("mock-live.transcript.md")) return;
    const t = setInterval(() => setDocNonce((n) => n + 1), 2500);
    return () => clearInterval(t);
  }, [mock, docPath]);

  // Entity badges and wiki links in chat/pages open the document HERE — minutes mode has no tabs,
  // so the pages panel is the one place a document can land. The chip is added to this room's page
  // list (so you can flip back) and opened.
  useEffect(() => {
    const onEntity = async (e: Event) => {
      const d = (e as CustomEvent<{ path?: string; wikilink?: string; slug?: string; docPath?: string; exact?: boolean }>).detail || {};
      // only the context the event carries: no `slug` key is "no workspace named", never the home one
      const r = await resolveDocRef(d, metaOfOpen(d)).catch(() => null);
      // NEVER A DEAD CLICK: an unresolved link opens its canonical path anyway, so the panel
      // answers with the empty state instead of the click vanishing (pageForDocRef).
      const pg = pageForDocRef(d, r);
      if (pg) openPage(pg, true);
    };
    const onMeeting = (e: Event) => {
      const ref = (e as CustomEvent<{ ref?: string }>).detail?.ref;
      if (!ref) return;
      const native = ref.includes("/") ? ref.slice(ref.indexOf("/") + 1) : ref;
      const m = meetings.find((x) => (x as { native_id?: string }).native_id === native || String(x.id) === ref);
      // same rule: a ref with no row behind it opens the meeting's notes page rather than nothing —
      // and asks the list for the row, so the next render of that ref can do better than the notes.
      // NEVER THE CENTRE (founder rule 2026-10-16): the meeting opens on the right; the chat stays.
      if (!m) ensureMeetingKnown(native);
      openPage(meetingRefPage(ref, meetings as never), true);
    };
    // A CLICK IS THE READER CHOOSING. Both of these arrive from something the person pressed in
    // the conversation, so from here on an artifact appends behind them rather than in front.
    const chose = (fn: (e: Event) => void) => (e: Event) => { readerChoseFocus.current = true; fn(e); };
    const onEntityClick = chose((e) => { void onEntity(e); });
    const onMeetingClick = chose(onMeeting);
    window.addEventListener(OPEN_ENTITY_EVENT, onEntityClick);
    window.addEventListener(OPEN_MEETING_EVENT, onMeetingClick);
    return () => { window.removeEventListener(OPEN_ENTITY_EVENT, onEntityClick); window.removeEventListener(OPEN_MEETING_EVENT, onMeetingClick); };
  }, [meetings, openMeeting, openPage]);

  /** THE CHAT THAT MADE THIS MEETING BECOMES ITS CHAT (Vexa-ai/vexa#1597).
   *
   *  Founder, 2026-09-06, in a live Meet he had started FROM a chat: *"if chat is a specific meeting
   *  — and that's a chat feature that it gets after creating meeting from itself — this transcript
   *  should be pinned. and the chat itself should be Live (left sidebar), while there is no need to
   *  create a new chat for that — we already have meeting owner, just attach the status to it"*. His
   *  rail showed the conversation AND an auto-created meeting row for the meeting it had made.
   *
   *  Four writes, and each one is a consumer of the ref that was missing:
   *    · the RECORD takes the meeting — the rail claims it (one row), wears `live`, and the header
   *      wears the badge, all of them from `railRows`/`flavor` reading the ref they always read;
   *    · the SELECTION becomes a meeting's, so the chips and the room layout key off it now rather
   *      than at the next open;
   *    · the meetings list is ASKED for the row, which it has never listed — the bot went out a
   *      second ago;
   *    · and the meeting view joins the strip, PINNED (`withMeetingPages` — it adds behind the
   *      reader and never re-opens the room, so the view the send just fronted stays in front).
   *
   *  A LATCH, exactly as `bindMeeting` and the server's index are: a chat already about a meeting is
   *  not rebound by a second send. Its identity is what the reader is looking at.
   *
   *  ONE VIEW (2026-10-11): the meeting's furniture is the meeting view, which reads its own notes
   *  (minted at bot-send, #1601) — so binding needs no note lookup and fronts nothing itself. */
  const bindMeetingHere = useCallback(async (meetingId: string) => {
    const id = selRef.current.chatId;
    if (!id || selRef.current.meetingId) return;
    const d = draftRef.current;
    if (d && d.id === id) setDraft((cur) => (cur && cur.id === id ? { ...cur, meeting: meetingId } : cur));
    persist((prev) => bindMeeting(prev, id, meetingId));
    setSel((x) => (x.chatId === id ? { ...x, kind: "meeting", meetingId } : x));
    ensureMeetingKnown(meetingId);
    const m = meetingsRef.current.find((x) => String(x.id) === meetingId);
    // A row the list has not caught up with is a meeting that has just begun — which is the only way
    // to reach this at all, so `live` is the honest reading rather than a default.
    const phase = m ? meetingPhase(m) : "live";
    // ONE VIEW: what binding adds is the meeting view, pinned behind whatever the reader is looking
    // at. The send's own `meeting:<row>` artifact is what fronts it, and that is the same page.
    const room = meetingPages(phase, meetingId, null, null);
    setPages((prev) => withMeetingPages(prev as Artifact[], room) as Page[]);
  }, [persist]);

  // ── F41, AS AMENDED BY DECISION 28: A FILE THE TURN WROTE NAVIGATES THE VIEW ────────────────
  //
  //  F41 was the founder creating a shared workspace, the agent writing its README, and the right
  //  panel staying on `_global/README.md` — the one document the turn had just made was the one
  //  thing not on screen. That fix appended a tab. Decision 28 is the correction TO that fix: a tab
  //  exists only when one is asked for, so the write moves the view instead.
  //
  //  Two rules, and the second is the one worth stating:
  //    · `focus: true` moves the view; `focus: false` does NOTHING VISIBLE. It used to append a tab
  //      "quietly behind the reader", which is exactly the accumulation 28 removes — a turn that
  //      writes four files must not leave four tabs nobody asked for.
  //    · …and never over a focus the READER chose. A person who has opened something is reading it;
  //      their attention beats our suggestion.
  //
  //  The decision itself is `artifactViewEffect` — pure, in roomView.ts, tested there. This is the
  //  wiring, and it is now one line: hand it to `openPage`, the ONE route into the panel, which
  //  unfolds a collapsed column, pushes back/forward history and records the visit in the strip.
  useEffect(() => {
    const onArtifact = (e: Event) => {
      const detail = (e as CustomEvent<{ workspace?: string; path?: string; focus?: boolean }>).detail || {};
      // …AND AN ARTIFACT THAT NAMES A MEETING BINDS ONE (Vexa-ai/vexa#1597). The harness emits this
      // for a successful `bot_send` and for nothing else, so it says: a bot went into a room BECAUSE
      // this conversation asked for one. That is the chat becoming the meeting's, and it happens
      // whatever the event asks the panel to do — before `artifactViewEffect`, which can legitimately
      // decide to move nothing (a reader who has since opened something else).
      const named = pageForArtifact(detail);
      if (named?.kind === "meeting") void bindMeetingHere(named.path);
      // A FILE THE TURN WROTE IS A CHIP IN THE CHAT, NOT A TAB (UI decision 7 amended): the panel
      // opens on the person's click, or for a meeting the chat just started.
      if (!autoOpens(detail)) return;
      const eff = artifactViewEffect(detail, readerChoseFocus.current, selRef.current.meetingId);
      if (!eff) return;              // neither pinned nor focused ⇒ NOTHING VISIBLE, not a quiet tab
      // PIN FIRST, then focus. `touchHistory` (inside openPage) carries a prior entry's `pinned`
      // forward, so pinning before navigating keeps the pin; the other order would move the page
      // into the strip unpinned and then pin a second copy of the same identity.
      if (eff.pin) {
        const a = { kind: eff.pin.kind, path: eff.pin.path, slug: eff.pin.slug, label: eff.pin.label };
        setPages((prev) => prev.some((x) => artifactKey(x) === artifactKey(a))
          ? prev.map((x) => (artifactKey(x) === artifactKey(a) ? { ...x, pinned: true } : x))
          : touchHistory(prev, { ...a, pinned: true }, Date.now()));
      }
      if (eff.view) openPage(eff.view);
    };
    window.addEventListener(ARTIFACT_EVENT, onArtifact);
    return () => window.removeEventListener(ARTIFACT_EVENT, onArtifact);
  }, [openPage, bindMeetingHere]);

  // ── THE AGENT OPENS SOMETHING BECAUSE IT WAS ASKED TO (Vexa-ai/vexa#1586) ────────────────────
  //
  //  The founder typed "open meeting transcript". The agent read the 677 segments, summarised them
  //  in prose and offered to re-verify facts; he answered *"it did not open the transcript"*. It
  //  could not: every panel move it had was a side effect of writing or sending something, so asked
  //  to SHOW a thing the only move available was to describe it.
  //
  //  This is the other end of `open_page`. Two lines of difference from the artifact effect above,
  //  and both follow from who asked:
  //    · it ALWAYS fronts. There is no `focus` flag to weigh — the event exists because a person
  //      asked for this page, and a suggestion's manners are the wrong manners for an instruction.
  //    · it beats `readerChoseFocus`, and then SETS it. The rule that guards a reader's attention
  //      from the agent's writes would here guard them from the thing they just asked for; and
  //      once this page is in front, it is what they chose, so the next quiet write still defers.
  //
  //  Resolution is the server's — the tool answered with a workspace and a path, including the
  //  `meeting:<row>` form for the canvas — so this reads `pageForArtifact` and re-derives nothing.
  //  A page the tool could not find never reaches here at all: it came back to the model as
  //  "no transcript for this meeting" and the panel was never told to move.
  useEffect(() => {
    const onOpen = (e: Event) => {
      const d = (e as CustomEvent<{ workspace?: string; path?: string }>).detail || {};
      // `browser-session:<id>` is the live sign-in view (ADR-0049), not a page.
      const bs = browserSessionFromOpenPath(d.path);
      if (bs) { openBrowserSession(bs); return; }
      // `chat:<id>` — a chat the agent just started for the person ("start an incognito chat").
      const chatId = /^chat:(pchat-[0-9a-z]{4,20})$/.exec(d.path ?? "")?.[1];
      if (chatId) { addChatRef.current?.("New chat", [], { id: chatId }); return; }
      const pg = pageForArtifact(d);
      if (!pg) return;
      readerChoseFocus.current = true;
      openPage(pg, true);
    };
    window.addEventListener(OPEN_PAGE_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_PAGE_EVENT, onOpen);
  }, [openPage]);

  // ── A WORKSPACE MADE FROM THIS CHAT IS IN THIS CHAT (Vexa-ai/vexa#1603) ──────────────────────
  //
  //  The founder asked for *"a new workspace where we will collect everything we know about Copperline"*.
  //  The agent made it — and then had to tell him *"the new workspace isn't in my native mount
  //  stack (it's reached via the workspace_* tools)"*. *"not native workspace??"*.
  //
  //  Creating a place IS bringing it into the room, so the create does here what a send does for a
  //  meeting: it widens THIS chat's focus. `setWorkspaces` is the one writer of that set — the same
  //  function the header chip's + calls — so the chip, the record and the server's mount set move
  //  together and by the same route, which is why nothing here touches `sel` or `persist` directly.
  //
  //  IT ALSO OPENS THE PLACE. A workspace is not a page, so there is nothing for `pageForArtifact`
  //  to resolve — its README is the door, the same page `homeEntry` makes the home of a chat that
  //  mounts a group. It goes through `openPage` like every other panel move, and only when the
  //  reader has not chosen a focus of their own: this is the turn's suggestion, not their ask, and
  //  their attention beats it exactly as it beats an artifact's.
  useEffect(() => {
    const onFocusWorkspace = (e: Event) => {
      const d = (e as CustomEvent<{ workspace?: string; name?: string }>).detail || {};
      const wid = d.workspace?.trim();
      if (!wid) return;
      setWorkspacesRef.current((ws) => (ws.includes(wid) ? ws : [...ws, wid]));
      // …AND IT BECOMES WHERE THIS CHAT WRITES (Vexa-ai/vexa#1611). A `focus` says "this workspace
      // is where this conversation is working", which has always meant both halves — it joins the
      // set, and it takes the target. `workspace_target` emits the same event for the same reason,
      // so *"work in the Example Bank workspace"* and *"make me a workspace for Example Bank"* land identically.
      // The mount goes in first, above, because `chooseTarget` refuses a target the chat is not
      // over — the record and the server both make that refusal, so the order is load-bearing.
      setChatTargetRef.current(wid, { justMounted: true });
      // The label is the workspace's HUMAN name when the create knew one — a tab reading
      // `copperline-effects-4040f4` is the slug leaking into a place names belong (F49).
      if (!readerChoseFocus.current) {
        openPage({ path: "README.md", slug: wid, label: d.name?.trim() || wid } as Page);
      }
    };
    window.addEventListener(FOCUS_WORKSPACE_EVENT, onFocusWorkspace);
    return () => window.removeEventListener(FOCUS_WORKSPACE_EVENT, onFocusWorkspace);
  }, [openPage]);

  // `?meeting=<ref>` — App.tsx stashed the ref before cleaning the URL. The list arrives
  // asynchronously, so this waits for it rather than firing once on mount — and is spent on the
  // first non-empty list either way, so a ref for a meeting this account cannot see never hijacks a
  // later session. A link the USER clicked counts as touching the chat it opens: the alternative is
  // a row that vanishes behind the filter the moment they navigate away.
  const meetingRefSpent = useRef(false);
  useEffect(() => {
    if (meetingRefSpent.current || !meetings.length) return;
    meetingRefSpent.current = true;
    let ref: string | null = null;
    let front: "meeting" | undefined;
    try {
      ref = localStorage.getItem("vexa.openMeetingRef"); localStorage.removeItem("vexa.openMeetingRef");
      // Set beside the ref by a share redeem (`App.stashSharedMeeting`): open on the meeting page.
      if (localStorage.getItem(SHARED_ARRIVAL_KEY) === "meeting") front = "meeting";
      localStorage.removeItem(SHARED_ARRIVAL_KEY);
    }
    catch { return; }
    if (!ref) return;
    const native = ref.includes("/") ? ref.slice(ref.indexOf("/") + 1) : ref;
    const m = meetings.find((x) => (x as { native_id?: string }).native_id === native || String(x.id) === ref);
    // The link is spent on the first non-empty list, so a row created between the click and that
    // list would be lost outright — ask for it rather than dropping the deeplink on the floor.
    if (m) void openMeeting(m, { touched: true, front }); else ensureMeetingKnown(native);
  }, [meetings, openMeeting]);

  // A VOICE NOTE (#1783) opens as its meeting the moment the list has its row: the recorder makes the
  // row, the list is asked for it, and this opens it — the transcript then arrives as for any meeting.
  const [pendingVoice, setPendingVoice] = useState<string | null>(null);
  // Two ways in, one recorder (founder 2026-10-13): the empty chat's "Record a voice note" chip and
  // the agent's `record_voice_note` tool both raise VOICE_NOTE_OPEN. A note already recording in this
  // tab is not started twice. A start that fails before the note exists (the microphone refused, the
  // service unreachable) has no meeting view to say so in: the rail says it (Rail's VoiceNoteLive).
  const startVoice = useCallback(() => {
    const phase = voiceNoteState().phase;
    if (phase === "starting" || phase === "recording" || phase === "stopping") return;
    // STARTED FROM A CHAT, IT IS THAT CHAT'S (founder, 2026-10-13): the note binds to the chat it was
    // started in — the header shows it, its transcript grounds the chat — and no chat of its own opens.
    const from = selRef.current?.chatId || "";
    void startVoiceNote(browserVoiceNoteDeps(), { speakerName: speakerRef.current }).then((m) => {
      ensureMeetingKnown(m.native_meeting_id);
      refreshMeetings();
      if (from && !from.startsWith("meet-") && m.id) {
        persist((prev) => bindMeeting(prev, from, String(m.id)));
        void bindMeetingToChat(from, String(m.id)).catch(() => { /* the note records either way */ });
        return;
      }
      setPendingVoice(m.native_meeting_id);
    }).catch(() => { /* the rail shows why the note did not start */ });
  }, [persist]);
  // THE MEETING PAGE LINKS BACK TO ITS CHAT: the rail's own row for the meeting is the chat it was
  // started from when there is one (`railRows` folds them), else its own chat.
  useEffect(() => {
    const open = (e: Event) => {
      const id = String((e as CustomEvent<{ meetingId?: string }>).detail?.meetingId || "");
      const m = meetings.find((x) => String(x.id) === id);
      if (m) void openMeeting(m, { touched: true });
    };
    window.addEventListener(OPEN_MEETING_CHAT, open);
    return () => window.removeEventListener(OPEN_MEETING_CHAT, open);
  }, [meetings, openMeeting]);
  useEffect(() => {
    window.addEventListener(VOICE_NOTE_OPEN, startVoice);
    return () => window.removeEventListener(VOICE_NOTE_OPEN, startVoice);
  }, [startVoice]);
  useEffect(() => {
    if (!pendingVoice) return;
    const m = meetings.find((x) => (x as { native_id?: string }).native_id === pendingVoice);
    if (m) { setPendingVoice(null); void openMeeting(m, { touched: true }); }
  }, [pendingVoice, meetings, openMeeting]);

  // A CALL (ADR-0048 decision 6): the dock stays across chats. "Private" opens my own chat about the
  // call's meeting — the meeting's chat, opened the way a voice note's is, once the list has its row.
  const [pendingCallMeeting, setPendingCallMeeting] = useState<string | null>(null);
  const openCallPrivate = useCallback((_meetingId: number, room: string) => {
    setPendingCallMeeting(room);
    ensureMeetingKnown(room);
    refreshMeetings();
  }, []);
  useEffect(() => {
    if (!pendingCallMeeting) return;
    const m = meetings.find((x) => (x as { native_id?: string }).native_id === pendingCallMeeting);
    if (m) { setPendingCallMeeting(null); void openMeeting(m, { touched: true }); }
  }, [pendingCallMeeting, meetings, openMeeting]);
  const openCallShared = useCallback((chatId: string) => {
    const c = chatsRef.current.find((x) => x.id === chatId);
    if (c) void openChat(c);
  }, [openChat]);

  // ── THE PANEL BELONGS TO THE AGENT ON ITS LEFT (founder, 2026-10-15) ─────────────────────────
  //  With nothing open, the panel lists THIS chat's artifacts: files its turns wrote (re-emitted by
  //  the chat surface, which owns the turns), pages opened in it (its own strip), cards bound to it
  //  and the meeting it is about. Switching chats switches the list; nothing comes from a workspace.
  const [chatWrites, setChatWrites] = useState<Record<string, { role: string; at?: number; ops?: { file?: string; wrote?: boolean }[] }[]>>({});
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ session?: string; turns?: { role: string; at?: number; ops?: { file?: string; wrote?: boolean }[] }[] }>).detail;
      if (!d?.session) return;
      setChatWrites((prev) => ({ ...prev, [d.session as string]: d.turns ?? [] }));
    };
    window.addEventListener(CHAT_ARTIFACTS_EVENT, on);
    return () => window.removeEventListener(CHAT_ARTIFACTS_EVENT, on);
  }, []);
  const artifactsHere = useMemo<ChatArtifact[]>(() => chatArtifacts({
    writes: writesOf(chatWrites[sel.chatId] ?? []),
    visits: pages,
    cards: (homeRead?.doc.cards ?? []).filter((c) => c.result?.chat === sel.chatId)
      .map((c) => ({ id: c.id, title: cardTitle(c), at: c.patchedAt, page: c.result?.page })),
    meeting: chatMeeting ? { id: String(chatMeeting.id), title: meetingTitle(chatMeeting) } : undefined,
  }), [chatWrites, sel.chatId, pages, homeRead, chatMeeting]);
  const openChatArtifact = (a: ChatArtifact) => {
    readerChoseFocus.current = true;
    if (a.meeting && chatMeeting) { void openMeeting(chatMeeting); return; }
    if (a.page) { openPage(a.page as Page, true); return; }
    // a written file or a card's page opens the way the chat's own chip does
    if (a.file) window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { path: a.file } }));
  };
  const session = sel.chatId;

  // The header says which phase the room is in. It read isHeld() — a two-way test — so a LIVE
  // meeting announced itself as "upcoming" beside a transcript that was visibly filling. Three
  // states now, in the meeting's own vocabulary.
  const PHASE_WORD = { prep: "upcoming", live: "live", post: "held" } as const;
  // THE CHAT SAYS WHAT IT IS; the header does not deduce it. This read `workspaces.filter(w => w !==
  // "_global").length === 0 ? "chat · admin" : "chat"` — mount arithmetic — and it was wrong the
  // moment the company-setup conversation legitimately mounted the admin's own desk beside
  // `_global` (the two-scaffold ruling: the first chat writes both layers). The instance's most
  // consequential conversation then announced itself as an ordinary "chat · personal".
  const selChat = allChats.find((c) => c.id === sel.chatId) ?? (draft?.id === sel.chatId ? draft : undefined);
  // ⚠ THE MOUNT-ARITHMETIC FALLBACK IS DELETED, not merely unreachable (F37). It read
  // `workspaces.filter(w => w !== "_global").length === 0 ? "chat · admin" : "chat"`, and it is how
  // a PLANTED "Organisation setup" row — a row with no scaffold record behind it — rendered as
  // `CHAT · ADMIN` and fell through to the pre-scaffold admin card that offered a research step
  // which does not exist. Founder: *"I explain this as stale code."* Now the record is the only
  // authority, and `Chat.scaffold` pairs the kind with the record's id so an admin-flavoured chat
  // with no scaffold behind it cannot be written in the first place.
  // A ROW THE LIST HAS NOT CAUGHT UP WITH GETS NO PHASE WORD (Vexa-ai/vexa#1597). This said "held"
  // — a reasonable default while every meeting chat was opened from a row the list already had, and
  // wrong the moment a chat can BIND a meeting the bot entered a second ago: the header announced a
  // meeting as finished while its transcript filled beside it. `meeting` alone is true either way.
  const flavor = sel.kind === "meeting" ? (selMeeting ? `meeting · ${PHASE_WORD[meetingPhase(selMeeting)]}` : "meeting")
    : selChat?.scaffold?.kind === "admin-setup" ? "chat · admin" : "chat";

  // PRD DECISION 30 — THE SURFACE WOULD BE A FACT THE SERVER HOLDS, not something the prompt
  // re-describes. Every change to what the human is looking at would be written to the session
  // record: which chat, which meeting and phase, the view, the strip's history and pins, the
  // navigator. Debounced and fire-and-forget inside `syncSurface`.
  //
  // ⚠ DECISION 30 IS NOT SHIPPED — `PUT /api/sessions/<id>/surface` is served by no service in
  // this repo — so this reads the gate ITSELF and returns before doing the work. It used to
  // recompute `orderHistory(pages)` and build the whole body on every change of chat, view, strip
  // or navigator and hand it to a `syncSurface` that dropped it on the floor: the code read as
  // wired while the agent was told nothing (2026-09-02 review, R-C09). The prompt keeps its
  // "Active context" prefix off the same flag; one value flips both halves.
  //
  // `strip.history` is at most ONE entry since the Obsidian ruling — the preview slot. The shape is
  // left alone because nothing consumes it yet and the field still means what it meant: what is in
  // the strip that nobody asked to keep.
  useEffect(() => {
    if (!SURFACE_RECORD_LIVE) return;
    const ordered = orderHistory(pages as Artifact[]);
    const ref = (a: Artifact) => ({ workspace: a.slug ?? "", path: a.path, title: a.label });
    syncSurface(sel.chatId, {
      chat: { id: sel.chatId, kind: sel.kind },
      meeting: sel.meetingId ? { id: sel.meetingId, phase: selMeeting ? meetingPhase(selMeeting) : null } : null,
      view: docPath ? { workspace: docSlug ?? "", path: docPath, title: (docPath.split("/").pop() || docPath).replace(/\.md$/i, "") } : null,
      strip: {
        history: ordered.filter((a) => !a.pinned && !a.desk).map((a) => ({ ...ref(a), at: a.at ?? 0 })),
        pins: ordered.filter((a) => a.pinned || a.desk).map(ref),
      },
      navigator: { open: pagesVisible, workspace: docSlug ?? null },
    });
  }, [sel.chatId, sel.kind, sel.meetingId, selMeeting, pages, docPath, docSlug, pagesVisible]);


  // `?ask=<preset>` — the emailed link. App.tsx stashed the name; resolve it to an ADMIN-AUTHORED
  // body in `_global/asks/<name>.md` and open a fresh chat already holding it. The preset also says
  // which workspaces the chat is over, so context and opening prompt arrive together — which is the
  // whole point of the link. Editing the file changes every future click; nothing is rebuilt.
  // The chat is created TOUCHED: today nothing distinguishes a link the user clicked from one a
  // flow injected, and the safe reading of an ambiguous case is "the human meant to be here".
  const presetFired = useRef(false);

  /** THE ONE PATH from "a link was clicked" to "a chat exists" (PRD §5.5 step 3).
   *
   *  Both entry points end here: `?s=<id>` with a server-minted scaffold, and the `?ask=&meeting=`
   *  hand link with a local one. Before this they were two bodies of composition code that had
   *  already drifted — only one of them knew about tabs — and that drift is the class of defect the
   *  scaffold record exists to end. If a third arrival is ever added, it mints a scaffold and calls
   *  this; it does not grow a third composer. */
  const openFromScaffold = useCallback((sc: Scaffold) => {
    const rec = scaffoldToChat(sc);
    // Decision 18's rule: a chat that already carries tabs belongs to its reader, and a second
    // arrival must not tidy their desk out from under them.
    const existing = chatsRef.current.find((c) => c.id === rec.id);
    const fresh = !existing?.artifacts.length;
    addChat(rec.label, rec.workspaces, {
      id: rec.id,
      meeting: rec.meeting,
      artifacts: fresh ? rec.artifacts : undefined,
      focus: fresh ? rec.focus : undefined,
      // THE FIRST VISIT IS THE HOME (#1783, founder 2026-10-13: "this same first page is the
      // onboarding"). Its greeting turn is not fired: the empty chat shows the home, whose first
      // cards are the onboarding curriculum's opening steps (`_global/onboarding.md`).
      kick: homeFirstVisit(sc) ? undefined : sc.openingText,
      // The record travels ONTO the chat — kind and id together (F37). It never used to: the kind
      // was computed here and dropped on the floor, which is why the header could only fall back to
      // mount arithmetic and why a planted row could wear the admin flavour without a record.
      scaffold: rec.scaffold,
      // EVERY kind rides its id onto the first turn. The hand-link exception here was the whole
      // defect of 2026-09-06: the claim route mints the admin-setup scaffold AS a hand-link, the id
      // was dropped, and agent-api never prepended the facts block — so the setup agent opened
      // with no idea whom it was talking to and reached for the mailbox address. The record has
      // `who` and `domain` on it; the only thing that ever withheld them was this line.
      scaffoldId: sc.id,
    });
    // AND THE OFFER IS SPENT, for the same reason a chip click spends it (`runProposal`): the first
    // move has been made. The chips render in the void an empty conversation leaves, and an
    // arrival's kick is hidden and settle-delayed — so without this the chat a link just opened
    // sits for a beat under "Set up a workspace for me", inviting the reader to start the thing
    // they have this second been sent.
    if (!homeFirstVisit(sc)) setSpent(rec.id);
  }, [addChat]);

  // A scaffold that would not open. Held so the reader is TOLD — a person who clicked a real link
  // and landed on a blank chat cannot tell a spent invitation from a broken product.
  const [scaffoldRefusal, setScaffoldRefusal] = useState<ScaffoldRefusal | null>(null);

  // WHO THE SERVER THINKS IS ASKING (F48). A refusal is a judgement about an identity, and the
  // reader cannot see which identity that was — so the card names it. Probed only when a refusal
  // actually exists: the common path is a link that opens, and it should cost nothing. Fail-soft —
  // an identity probe that does not answer leaves the copy exactly as it read before.
  // IS THIS THE DEPLOYMENT'S ADMIN ACCOUNT (founder ruling 2026-10-11: *"admin is a special account
  // that has global only"*) — the one question the header turns on. The admin account sees Company
  // and nothing else; every everyday account (the instance admin's included) never sees Company.
  // FAIL CLOSED: `admin_account` is three-valued (see /api/auth/me) and only a literal `true` counts.
  // Nothing hangs on it being right — the server confines the admin account and refuses everybody
  // else's `_global` writes either way — so it is a probe, not a gate.
  const [isAdminAccount, setIsAdminAccount] = useState(false);
  useEffect(() => {
    let active = true;
    void fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (active) setIsAdminAccount((d as { admin_account?: boolean } | null)?.admin_account === true); })
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  const [signedInAs, setSignedInAs] = useState<string | null>(null);
  useEffect(() => {
    if (!scaffoldRefusal) return;
    let active = true;
    void fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (active) setSignedInAs((d?.user?.email as string | undefined) ?? null); })
      .catch(() => undefined);
    return () => { active = false; };
  }, [scaffoldRefusal]);

  // `?s=<id>` — THE SCAFFOLD (PRD §5.5 step 3). One record per arrival: the server says which
  // workspaces, which documents, and what the opening is; the terminal renders a chat from it and
  // NOTHING here is composed from what was there before. The right panel keeps rendering from the
  // chat record only — the decision-18 contract — so this end of the arrival writes that record
  // correctly, or says why it could not, and then gets out of the way.
  //
  // THE HANDOVER IS IN `arrival.ts`, and so is the reason this is no longer an effect of its own.
  // This component is a CHILD of the one that reads the URL and React runs a child's effects first,
  // so the version that took the id out of storage on READ then lost it — along with the request it
  // had already sent — to the parent's clean-up reload, and the reader saw an empty chat. The hook
  // checks the stash AND waits to be told, and settles the stash on the server's answer rather than
  // on having read it, so neither order and no torn-down document can drop an arrival.
  //
  // NO WAIT ON THE MEETINGS LIST. The record carries `native` itself, so nothing here needs the
  // list to resolve the note tab — which matters because the list is exactly what may not have
  // loaded yet when an emailed link lands. One record, and it is complete. (The old effect
  // re-ran on the meetings list for no stated reason; what that actually bought was a second look
  // at a stash that had not arrived yet — an accident doing, badly, the job the hook now does.)
  //
  // (The scaffold used to CLAIM the opening here — `setPresetInFlight(true)` — so the cached
  // greeting would stand down. There is no greeting left to race: F36 deleted it.)
  useScaffoldArrival({ onOpen: openFromScaffold, onRefuse: setScaffoldRefusal });

  useEffect(() => {
    if (presetFired.current) return;
    let raw: string | null = null;
    try { raw = localStorage.getItem("vexa.pendingPreset"); } catch { /* ignore */ }
    if (!raw) return;
    let intent: { ask?: string; ws?: string; meeting?: string };
    try { intent = JSON.parse(raw) as typeof intent; } catch { presetFired.current = true; return; }
    const name = (intent.ask || "").trim();
    // a NAME, and only a name — no slashes, no dots, nothing that walks out of asks/
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/i.test(name)) { presetFired.current = true; return; }
    presetFired.current = true;
    try { localStorage.removeItem("vexa.pendingPreset"); } catch { /* ignore */ }
    void (async () => {
      // THE CLIENT COMPOSES NOTHING (decisions 13/18, F97).
      //
      // This effect used to read the preset itself and substitute `?meeting=` and `?ws=` straight
      // into the opening text — so a crafted `/?ask=prep&meeting=<payload>` put attacker-chosen
      // words into the agent's first turn. The URL carries NAMES, never prompt text, and the `?s=`
      // path already obeyed that while this one did not.
      //
      // Now the hand link MINTS, server-side, for the signed-in caller: the server reads the
      // preset, decides the mounts, refuses a meeting this person cannot open, and substitutes at
      // turn time out of the RECORD. We hand it two names and take back an id.
      //
      // `?ws=` is gone entirely rather than forwarded — the mount set is the server's to derive,
      // and it was the second thing a URL could dictate.
      let res: Response;
      try {
        res = await fetch("/api/scaffolds/hand", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ preset: name, meeting: (intent.meeting || "").trim() || null }),
        });
      } catch (e) {
        console.error("hand link could not be minted:", e);
        setScaffoldRefusal({ reason: "unavailable", status: 0, detail: e instanceof Error ? e.message : String(e) });
        return;
      }
      if (!res.ok) {
        // A link that cannot mint opens NOTHING and says so — the same rule the `?s=` path follows.
        // Silently falling back to a greeting would leave the reader believing they had arrived.
        let detail = `HTTP ${res.status}`;
        try { const b = await res.json() as { detail?: string }; if (b?.detail) detail = b.detail; } catch { /* keep the status */ }
        console.error("hand link refused:", res.status, detail);
        setScaffoldRefusal({
          reason: res.status === 404 ? "not-found" : res.status === 401 || res.status === 403 ? "forbidden" : "unavailable",
          status: res.status, detail,
        });
        return;
      }
      const { id } = await res.json() as { id?: string };
      if (!id) {
        setScaffoldRefusal({ reason: "malformed", status: res.status, detail: "the mint returned no id" });
        return;
      }
      // ONE COMPOSITION PATH, arrived at by redirect: `/?s=<id>` is the path every emailed link
      // already takes, so the hand link stops being a second way of opening a chat and becomes a
      // way of MINTING one. Nothing downstream needs to know it was a hand link.
      window.location.replace(`/?s=${encodeURIComponent(id)}`);
    })();
  }, []);

  // THE RAIL, in whichever form fits: docked in the grid, or inside the drawer the strip (or the
  // header's menu control, in single mode) opens. Selecting a chat from the drawer closes it — the
  // reader came for the conversation, not the list.
  const railBody = (inDrawer: boolean) => (
    <Rail rows={rows} folders={railFolders}
      onNewRoom={roomsOn ? () => { if (inDrawer) shell.closeOverlays(); setRoomDialog({ kind: "new" }); } : undefined}
      onBrowseRooms={roomsOn ? () => { if (inDrawer) shell.closeOverlays(); setRoomDialog({ kind: "browse" }); } : undefined}
      selKey={selKey} onSelect={(r) => { if (inDrawer) shell.closeOverlays(); void openRow(r); }}
      onHome={home2 ? () => {
        if (inDrawer) shell.closeOverlays();
        goHome();
        // shell2 (UI 7t): "+ New" always opens the FULL home aimed at My desk — a remembered target or
        // focus never carries over
        if (shell2) { closeCentre(); window.setTimeout(() => aimHome(HOME_PERSONAL), 0); }
      } : undefined}
      onNavigate={inDrawer ? () => shell.closeOverlays() : undefined}
      onNewWorkspace={home2 ? () => { if (inDrawer) shell.closeOverlays(); goHome(); window.setTimeout(() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: NEW_WORKSPACE_KICK, display: "Set up a new workspace" } })), 80); } : undefined}
      onNewChat={() => { if (inDrawer) shell.closeOverlays(); startDraft(); }} onDeleteChat={(id, o) => void deleteChat(id, o)} onMove={reorder}
      onDeleteChats={(ids) => void (async () => { for (const id of ids) await deleteChat(id, { retireRoutine: false }); })()}
      onShareChats={(rs, email) => void (async () => {
        setOrderError("");
        const failed = (await Promise.allSettled(rs.filter((r) => r.chatId).map((r) => addParticipant(r.chatId as string, { email }))))
          .filter((x) => x.status === "rejected").length;
        if (failed) setOrderError(`Couldn't share ${failed} of ${rs.length} chats. Try again.`);
      })()}
      meetingActsFor={meetingActsFor}
      onPin={(r, on) => void mark(r.key, { pinned: on })} onHide={(r, on) => void mark(r.key, { hidden: on })}
      {...(home2 && !folders.served ? { onMoveRows } : folders.served ? {
        onMoveRows,
        onFolderCreate: (name: string, then?: string[]) => void folderAct(async () => { const f = await createFolder(name); if (then?.length) await moveRows(then, f.id); }, "Couldn't make the folder. Try again."),
        onFolderRename: (id: string, name: string) => void folderAct(() => updateFolder(id, { name }), "Couldn't rename the folder. Try again."),
        onFolderDelete: (id: string) => void folderAct(() => deleteFolder(id), "Couldn't delete the folder. Try again."),
        onFolderCollapse: (id: string, collapsed: boolean) => void updateFolder(id, { collapsed }).catch(() => {}),
        onFolderMove: (id: string, position: number) => void folderAct(() => updateFolder(id, { position }), "Couldn't move the folder. Try again."),
      } : {})}
      onCollapse={() => shell.setRailOpen(false)} collapseLabel={inDrawer ? "Close the chat list" : undefined} />
  );
  // the person's list, once per sign-in; a first load with nothing on the server keeps the pins the
  // open chat already had, so nobody loses a tab to the move
  const pagesRef = useRef<Page[]>([]);
  pagesRef.current = pages;
  useEffect(() => {
    if (!email) return;
    const seed = pagesRef.current.filter((a) => a.pinned && !a.desk && a.kind !== "meeting")
      .map((a) => ({ slug: a.slug ?? null, path: a.path, label: a.label }));
    void loadUserPins(seed);
  }, [email]);
  // ROOMS (R1): what the chat in front is; the dialogs; the personal header's "…"
  const room = useRoom(sel.chatId, roomsOn && draft?.id !== sel.chatId);
  const inRoom = roomsOn && !!room?.room;
  const roomWorkspaces = useMemo(() => {
    const shared = writable.filter((w) => memberships.some((m) => m.workspace_id === w.slug));
    return shared.length ? shared : writable.filter((w) => w.slug !== "personal");
  }, [writable, memberships]);
  const wsName = (slug: string) => (slug === "personal" || !slug) ? deskName() : writable.find((w) => w.slug === slug)?.name ?? slug;
  const [roomDialog, setRoomDialog] = useState<null | { kind: "new" | "take" | "browse"; workspace?: string; name?: string; from?: string }>(null);
  const setRoomDialogRef = useRef(setRoomDialog);
  const [activityOpen, setActivityOpen] = useState(false);
  const openRoomSession = (sid: string) => { setRoomDialog(null); void refreshRoom(sid); openSharedSession(sid); };
  const thinkPrivately = () => {
    // a private 1:1 about the room, linked back to it; it writes where you write
    const c = addChat(`Thinking about: ${sel.label || "the room"}`, ["personal", "_global"]);
    rememberThink(c.id, { session: sel.chatId, label: sel.label || "the room" });
  };
  const back = roomsOn ? thinkOf(sel.chatId) : null;
  // PINNED PAGES IN THE CHAT HEADER while the panel is closed (#1783): reachable without opening it.
  const pinChips = !pagesVisible && userPins.length > 0 ? (
    <span className="vx-pin-chips" role="group" aria-label="Pinned pages">
      {userPins.slice(0, 6).map((p) => (
        <button key={`${p.slug ?? ""}:${p.path}`} type="button" className="vx-pin-chip" title={`${p.label} — open`}
          onClick={() => openPage({ kind: "doc", path: p.path, slug: p.slug ?? undefined, label: p.label } as Page, true)}>
          {p.label || p.path.split("/").pop()}
        </button>
      ))}
    </span>
  ) : null;
  const pagesOverlay = L.pagesKind === "sheet" || L.pagesKind === "fullscreen";
  pagesFullRef.current = L.pagesKind === "fullscreen";
  // shell2 ON A PHONE: the bottom bar (decision 10), and system back closing the drawer or the sheet
  const bottomBar = shell2 && L.mode === "single";
  const sheetForm = L.pagesKind === "sheet" ? "overlay" : L.pagesKind === "fullscreen" ? "fullscreen" : "inline";

  return (
    <div ref={shellRef} data-shell-mode={L.mode} data-rail={L.railKind} data-pages={L.pagesKind}
      style={{ position: "relative", display: "grid", gridTemplateColumns: L.columns, gridTemplateRows: `${T.headerH}px 1fr${bottomBar ? " auto" : ""}`, height: "100%", minHeight: 0, minWidth: 0, overflow: "clip", background: surface.rail }}>
      <NotificationToasts />
      {L.railKind === "docked" ? railBody(false)
        : L.railKind === "strip" ? <RailStrip open={shell.drawerOpen} onOpen={() => shell.setRailOpen(true)}
          onNewChat={shell2 ? () => { closeCentre(); goHome(); window.setTimeout(() => aimHome(HOME_PERSONAL), 0); } : startDraft}
          extra={shell2 ? <StripNav /> : undefined} />
        : null}
      {L.railKind !== "docked" && (
        <Drawer open={shell.drawerOpen} onClose={() => shell.setRailOpen(false)} width={L.widths.drawer} label="Chats">
          {railBody(true)}
        </Drawer>
      )}
      {L.railKind === "docked" && L.bounds.rail && (
        <Splitter label="Resize chat list" value={L.widths.rail} bounds={L.bounds.rail} grows="right"
          onPreview={(w) => shell.previewWidth("rail", w)} onCommit={(w) => shell.commitWidth("rail", w)}
          style={{ left: L.widths.rail - 4 }} />
      )}
      {/* A RAIL ERROR IS A CALM TOAST (#1783): it never lands as raw text over the account card,
          it says what to do, and it can be dismissed (it also goes by itself). */}
      {orderError && <RailToast message={orderError} onDismiss={() => setOrderError("")} />}
      <div style={{display:connectionsMode==='page'&&!pagesOverlay?"none":"contents"}}>
      <MessageActionsHost />
      {inRoom && room ? <RoomHeader session={sel.chatId} label={sel.label} room={room} workspaceName={wsName(room.workspace)}
        onRename={renameChat} onThinkPrivately={thinkPrivately}
        onLeave={() => {
          // leaving is the server's act (an owner's room is handed on to the next member; the last
          // one cannot leave, and the server says why); the room then goes from this person's sidebar
          const sid = sel.chatId;
          void leaveRoom(sid).then(() => { persist((prev) => prev.filter((c) => c.id !== sid)); startDraft(); })
            .catch((e: unknown) => setOrderError(((e as { body?: { detail?: { said?: string } } })?.body?.detail?.said)
              || "Could not leave the room. Try again."));
        }}
        onActivity={() => setActivityOpen(true)}
        leading={L.railKind === "drawer" ? <ShellToggle kind="rail" open={shell.drawerOpen} onClick={() => shell.setRailOpen(!shell.drawerOpen)} /> : undefined} />
      : <ContextBar sel={sel} personal={roomsOn && sel.kind === "chat" ? { face: <Face name={signedInAs ?? email ?? ""} />, more: [
          { key: "r-call", label: "Start a call", onSelect: () => window.dispatchEvent(new CustomEvent(CALL_START_EVENT, { detail: { session: sel.chatId } })) },
          { key: "r-take", label: "Take to a room…", onSelect: () => setRoomDialog({ kind: "take", workspace: sel.target, name: sel.label, from: sel.label }) },
          ...(back ? [{ key: "r-back", label: `Back to # ${back.label}`, onSelect: () => openSharedSession(back.session) }] : []),
          ...userPins.slice(0, 4).map((pp) => ({ key: `r-pin:${pp.slug ?? ""}:${pp.path}`, label: `Open ${pp.label || pp.path.split("/").pop()}`, onSelect: () => openPage({ kind: "doc", path: pp.path, slug: pp.slug ?? undefined, label: pp.label } as Page, true) })),
          { key: "r-activity", label: "Activity…", onSelect: () => setActivityOpen(true) },
        ] } : undefined} untitled={draft?.id === sel.chatId || /^new chat$/i.test((sel.label ?? "").trim())} onRename={draft?.id === sel.chatId ? undefined : renameChat} flavor={flavor} memberships={memberships}
        defaultTarget={defaultTarget} onMakeDefault={makeDefaultTarget}
        scope={isAdminAccount ? undefined : { session: sel.chatId, onReads: onScopeReads }}
        leading={L.railKind === "drawer"
          ? <ShellToggle kind="rail" open={shell.drawerOpen} onClick={() => shell.setRailOpen(!shell.drawerOpen)} /> : undefined}
        trailing={<>
          {chatMeeting && <LiveMeetingChip meeting={chatMeeting} onOpen={() => openMeetingView(chatMeeting, true)} />}
          {!roomsOn && pinChips}
          {pagesOverlay && <ShellToggle kind="pages" open={shell.sheetOpen} onClick={() => shell.setPagesOpen(!shell.sheetOpen)} />}
        </>}
        writable={writable}
        onWriteTo={(id) => {
          // MOUNT · TARGET, one act — the mount first because `chooseTarget` refuses a target the
          // chat is not over, and `justMounted` lets the second beat trust it before React commits.
          setWorkspaces((ws) => (ws.includes(id) ? ws : [...ws, id]));
          chooseTarget(id, { justMounted: true });
        }}
        onAddWorkspace={(id) => setWorkspaces((ws) => ws.includes(id) ? ws : [...ws, id])}
        onRemoveWorkspace={(id) => {
          setWorkspaces((ws) => ws.filter((w) => w !== id));
          // A REMOVED WORKSPACE CANNOT STAY THE TARGET (Vexa-ai/vexa#1611). The chat is no longer
          // over it, so the writes fall back to the desk — the default — rather than pointing at a
          // mount the next turn will not carry.
          if (sel.target === id) chooseTarget("");
        }}
        onSetTarget={(id) => chooseTarget(id)}
        adminAccount={isAdminAccount}
        onTargetGlobal={() => {
          // THE ADMIN AIMS A CHAT AT THE COMPANY LAYER (Vexa-ai/vexa#1616) — MOUNT · TARGET ·
          // OPEN, in that order, which is the same three beats the `focus` event performs and
          // for the same reasons. The mount goes first because `chooseTarget` refuses a target
          // the chat is not over and a restored row need not carry `_global` in its set at all;
          // `justMounted` is what lets the second beat trust the first before React commits it.
          //
          // The README opens UNCONDITIONALLY here, unlike the `focus` handler which yields to a
          // focus the reader chose: there the panel move is the turn's suggestion, here it IS
          // the reader's ask — they clicked the thing whose whole meaning is "take me there".
          setWorkspaces((ws) => (ws.includes(GLOBAL_MOUNT) ? ws : [...ws, GLOBAL_MOUNT]));
          chooseTarget(GLOBAL_MOUNT, { justMounted: true });
          readerChoseFocus.current = true;
          openPage({ path: "README.md", slug: GLOBAL_MOUNT, label: COMPANY_WORD } as Page, true);
        }}
        onAttachRepo={(id) => setAttachTo({ id })}
        onFolderPicked={(folder) => requestFolderImport({ kind: "folder", folder, into: null })} />}
      {roomDialog?.kind === "browse" && <BrowseRoomsDialog workspaces={roomWorkspaces} onClose={() => setRoomDialog(null)} onOpen={openRoomSession} />}
      {(roomDialog?.kind === "new" || roomDialog?.kind === "take") && <NewRoomDialog workspaces={roomWorkspaces} initialWorkspace={roomDialog.workspace}
        initialName={roomDialog.name ?? ""} from={roomDialog.kind === "take" ? { label: roomDialog.from ?? "" } : undefined}
        onClose={() => setRoomDialog(null)} onCreated={(sid) => openRoomSession(sid)} />}
      {/* A FOLDER DROPPED OR PICKED becomes a workspace (founder 2026-10-13). One host for the whole
          shell: the rail, the chat and the empty state all land here. Offered INTO the workspace
          this chat writes to when that is one; what it makes joins this chat's set. */}
      {!isAdminAccount && (
        <FolderImportHost
          chatTarget={sel.target && sel.target !== GLOBAL_MOUNT ? { slug: sel.target, label: <WorkspaceName slug={sel.target} /> } : null}
          onImported={(r) => { if (r.slug) setWorkspaces((ws) => (ws.includes(r.slug) ? ws : [...ws, r.slug])); }} />
      )}
            {/* Wrapped rather than a bare id so "the desk" (id undefined) is still an OPEN dialog — a
          nullable id alone cannot tell "no dialog" from "dialog, aimed at the desk". */}
      {attachTo && (
        <AttachRepo workspaceId={attachTo.id} onClose={() => setAttachTo(null)}
          onAttached={(id) => { if (id) setWorkspaces((ws) => ws.includes(id) ? ws : [...ws, id]); }} />
      )}
      <main className="vx-pane" data-pane="conversation" style={{ gridRow: 2, gridColumn: 2, minWidth: 0, minHeight: 0, background: surface.center, display: "flex", flexDirection: "column" }}>
        <DesktopToaster />
        <CallDock onOpenShared={openCallShared} onOpenPrivate={openCallPrivate}
          nameOf={(sid) => { const c = allChats.find((x) => x.id === sid); return (c as { label?: string } | undefined)?.label ?? ""; }} />
        {/* A SCAFFOLD THAT WOULD NOT OPEN STATES ITSELF. Someone who clicked a real link and landed
            on an empty conversation cannot tell a spent invitation from a broken product — and the
            second reading is the one they take. So: whose it is, and what to do about it. It sits
            ABOVE the chat rather than replacing it, because their own conversations are still
            theirs and hiding them would be a second wrong. */}
        {scaffoldRefusal && (
          <ScaffoldRefusalCard refusal={scaffoldRefusal} signedInAs={signedInAs}
            onDismiss={() => setScaffoldRefusal(null)} />
        )}
        {/* A ROUTINE'S OWN CHAT carries its controls at the top of the conversation — only that
            chat: it is part of this column, so it leaves with the chat when another one opens. */}
        {/* A ROUTINE'S OWN CHAT carries its controls at the top of the conversation. ROOMS (R1): they
            live under "…" → Activity in both headers instead of as a strip. */}
        {(() => {
          const strip = <ChatRoutines key={session} session={session} fallback={routineOfOpenChat(allChats, session)}
            stage={allChats.find((c) => c.id === session)?.concern?.stage} />;
          if (!roomsOn) return strip;
          return activityOpen ? <Modal title="Activity" onClose={() => setActivityOpen(false)} width={460}>{strip}</Modal> : null;
        })()}
        <BlocksProvider value={homeHandlers}>
        <div style={{ flex: 1, minHeight: 0 }}>
          {/* THE ADMIN ACCOUNT'S HOME (founder ruling 2026-10-11): its four areas in place of the
              everyday chips, which offer a desk and meetings it does not have. */}
          <Chat params={{ session }} emptyExtra={isAdminAccount ? <AdminHome /> : <DesktopHome
            /* THE DESKTOP (desktop ADR D1): behind `desktop`, in place of the composed cards; otherwise the home below */
            active={spent !== sel.chatId && draft?.id === sel.chatId} greeting={homeRead?.doc.greeting}
            starters={shownChips.length ? <ProposalChips items={shownChips} onPick={(p) => void runProposal(p)} onDismiss={dismissProposal} /> : undefined}
            extra={homeRead && homeRead.people !== false ? <PeopleStrip /> : undefined}
            fallback={homeRead && spent !== sel.chatId && draft?.id === sel.chatId
            ? <HomeStream doc={homeRead.doc} people={homeRead.people} starters={shownChips.length ? <ProposalChips items={shownChips}
                onPick={(p) => void runProposal(p)} onDismiss={dismissProposal} /> : undefined} />
            : <ProposalChips items={shownChips}
            onPick={(p) => void runProposal(p)} onDismiss={dismissProposal} />} />} />
        </div>
        </BlocksProvider>
      </main>
      {/* SHELL2 (UI 7k): the Inbox or Meetings, in the centre column over the conversation — its own
          header (the menu control in single mode), drawn after <main> so it paints above it, and
          below the pages sheet, so a card or a meeting opened from the list is never hidden */}
      {shell2 && centreList && <CentrePane kind={centreList}
        leading={L.railKind === "drawer" ? <ShellToggle kind="rail" open={shell.drawerOpen} onClick={() => shell.setRailOpen(!shell.drawerOpen)} /> : undefined}
        trailing={pagesOverlay ? <ShellToggle kind="pages" open={shell.sheetOpen} onClick={() => shell.setPagesOpen(!shell.sheetOpen)} /> : undefined} />}
      </div>
      {/* the pages panel's resize handle — a docked panel only: a folded or overlaid one has no
          edge to drag. */}
      {L.pagesKind === "docked" && L.bounds.pages && !connectionsOpen && (
        <Splitter label="Resize pages panel" value={L.widths.pages} bounds={L.bounds.pages} grows="left"
          onPreview={(w) => shell.previewWidth("pages", w)} onCommit={(w) => shell.commitWidth("pages", w)}
          style={{ right: L.widths.pages - 4 }} />
      )}
      {/* CONNECTIONS take the pages panel's place: docked in its column, or — where the panel is a
          sheet — as a sheet of their own. One element either way, so it never remounts. */}
      <Sheet form={connectionsOpen && pagesOverlay ? (connectionsMode === "page" ? "fullscreen" : sheetForm) : "inline"}
        open={connectionsOpen} onClose={() => window.dispatchEvent(new Event(CONNECTIONS_CLOSE))}
        width={L.widths.sheet} label="Connections" dataPane="connections">
        <ConnectionsPanel onModeChange={setConnectionsMode} />
      </Sheet>
      <BrowserSessionView />
      <div style={{display:connectionsOpen?"none":"contents"}} aria-hidden={connectionsOpen||undefined}>
      {L.pagesKind === "collapsed"
        ? <EdgeHandle side="right" onClick={() => shell.setPagesOpen(true)} />
        : <Sheet form={sheetForm} open={shell.sheetOpen} onClose={() => shell.setPagesOpen(false)}
            width={L.widths.sheet} label="Pages" dataPane="pages">
            <BlocksProvider value={homeHandlers}><PagesPanel roomSession={inRoom ? sel.chatId : undefined} pages={stripPages} docPath={docPath} docSlug={docSlug} docKind={docKind} chatReads={sel.workspaces}
              onTogglePin={togglePin} onOpen={(pg) => { readerChoseFocus.current = true; openPage(pg, true); }} onClose={closeTab}
              listing={listing} onNavigate={(slug, prefix) => void navigate(slug, prefix)}
              canBack={canBack} canForward={canForward} onBack={goBack} onForward={goForward}
              body={docBody} notice={notice} onSaved={() => setDocNonce((n) => n + 1)}
              onCollapse={() => shell.setPagesOpen(false)}
              artifacts={artifactsHere} onArtifact={openChatArtifact} phone={L.pagesKind === "fullscreen"}
              collapseLabel={pagesOverlay ? "Back to the conversation" : undefined} /></BlocksProvider>
          </Sheet>}
      </div>
      {bottomBar && <BottomBar home={draft?.id === sel.chatId} chatsOpen={shell.drawerOpen}
        onHome={() => { shell.closeOverlays(); closeCentre(); goHome(); window.setTimeout(() => aimHome(HOME_PERSONAL), 0); }}
 />}
    </div>
  );
}

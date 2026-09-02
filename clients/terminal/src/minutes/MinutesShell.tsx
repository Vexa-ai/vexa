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
import { WORKSPACE_WORD } from "./vocabulary";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ARTIFACT_EVENT, ASK_CHAT_EVENT, CHAT_TOUCHED_EVENT, WORKSPACE_COMMIT_EVENT, OPEN_ENTITY_EVENT, OPEN_MEETING_EVENT } from "../canvas/actions";
import { Chat } from "../surfaces/chat";
import { useLiveMeetings, useLiveMeetingsLoaded } from "../surfaces/liveMeetings";
import {
  readActiveSet, setSharedActive, deactivateWorkspace, readWorkspaceFile,
  listSharedMemberships, listWorkspaceTree, type Membership,
} from "../surfaces/workspaceApi";
import { AttachRepo } from "./AttachRepo";
import { ContextBar } from "./ContextBar";
import { PagesPanel, type Listing } from "./PagesPanel";
import {
  chatForRow, loadChats, loadCollapsed, loadRailAll, markTouched, meetingChatId, meetingTitle, nameChat, nameFromTurn,
  newChat, railRows, readRailOwner, resetChats, writeRailOwner,
  removeChat, saveChats, saveCollapsed, saveRailAll, upsertChat, visibleRows, artifactKey,
  forgetHistory, orderHistory, touchHistory, withHome,
  type Artifact, type Chat as ChatRec, type Row } from "./chats";
import { resolveDocRef } from "../ui-kit/docLinks";
import { syncSurface } from "../surfaces/surfaceSync";
import { Rail } from "./Rail";
import { ScaffoldRefusalCard } from "./ScaffoldRefusalCard";
import { meetingPhase, type MeetingMock } from "../surfaces/meetingModel";
import { fetchScaffold, localScaffold, scaffoldToChat, type Scaffold, type ScaffoldRefusal } from "./scaffold";
import { artifactsFromTokens, artifactViewEffect, pageForDocRef, pageForMeetingRef, pagesForPhase, resolveView, VIEW_KEY, VIEW_NAVIGATE_EVENT, type ViewSlot } from "./roomView";
import { deskPanelPages } from "./deskPanel";
import { reportOpened } from "./deskTouch";
import { applyProposal, proposals, type Proposal } from "./proposals";
import { ProposalChips } from "./ProposalChips";
import { EdgeHandle, EDGE_W } from "./Collapse";
import { MOCK_CHATS, MOCK_MEETINGS, mockBody, mockOn } from "./mockPhases";
import { T, maxPagesW, surface, type as ty } from "./tokens";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
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
  const [all, setAll] = useState<boolean>(() => loadRailAll());

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
  const [firstOpen] = useState<ChatRec>(() =>
    [...chats].sort((a, b) => (b.lastActivityAt || 0) - (a.lastActivityAt || 0))[0]
    ?? newChat("New chat", ["personal", "_global"], { touched: false }));
  const [draft, setDraft] = useState<ChatRec | null>(() =>
    (chats.some((c) => c.id === firstOpen.id) ? null : firstOpen));
  const draftRef = useRef<ChatRec | null>(draft);
  useEffect(() => { draftRef.current = draft; }, [draft]);
  const [sel, setSel] = useState<Sel>(() => selOf(firstOpen));
  const [pages, setPages] = useState<Page[]>([]);
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
  // Is a deeplink about to choose the first room? Read ONCE, here, before the effects that consume
  // these keys run — otherwise the boot below would race them and steal the room they were opening.
  const [deeplinkPending] = useState<boolean>(() => {
    try {
      return !!localStorage.getItem("vexa.openMeetingRef") || !!localStorage.getItem("vexa.pendingPreset") || !!localStorage.getItem(VIEW_KEY);
    } catch { return false; }
  });
  const [docPath, setDocPath] = useState("README.md");
  const [docSlug, setDocSlug] = useState<string | undefined>(undefined);
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
  // `.scaffolded` — written by the personal setup flow as its FINAL act, so its ABSENCE is the one
  // durable signal that this person has never been set up. Read ONCE, on mount: it is the only
  // input the proposal row needs that is not already in hand, and it costs a single workspace read.
  // `null` until it answers, and null never offers the chip (proposals.ts fails closed).
  const [scaffolded, setScaffolded] = useState<boolean | null>(null);
  useEffect(() => { void readWorkspaceFile(".scaffolded").then((c) => setScaffolded(c !== null)).catch(() => undefined); }, []);
  // The signed-in address — the ONE fact the setup chip puts in the person's mouth, so they never
  // type what we already know. Same seam the account badge reads; unknown simply drops the clause.
  const [email, setEmail] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (live) setEmail(((d?.user as { email?: string } | undefined)?.email) ?? null); })
      .catch(() => undefined);
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

  const rows = useMemo(() => railRows(allChats, meetings), [allChats, meetings]);
  const selKey = `c:${sel.chatId}`;
  const shownRows = useMemo(() => visibleRows(rows, all, selKey), [rows, all, selKey]);
  const hiddenCount = useMemo(() => rows.length - visibleRows(rows, false, selKey).length, [rows, selKey]);
  const toggleAll = (v: boolean) => { setAll(v); saveRailAll(v); };

  /** Persist a change to the stored list. Mock chats live outside it, so a mutation aimed at one is
   *  a no-op by construction — the mock is a display fixture, not a record. */
  const persist = useCallback((fn: (prev: ChatRec[]) => ChatRec[]) => {
    setChats((prev) => { const next = fn(prev); if (next !== prev) saveChats(next); return next; });
  }, []);

  // BOTH side columns fold away, independently, and the choice persists per side (founder,
  // 2026-09-01). Collapse never writes `pagesW`, so reopening the panel restores the width the
  // reader dragged it to — the two controls share a column and no state.
  const [attachTo, setAttachTo] = useState<{ id?: string } | null>(null);
  const [railCollapsed, setRailCollapsed] = useState<boolean>(() => loadCollapsed("left"));
  const [pagesCollapsed, setPagesCollapsed] = useState<boolean>(() => loadCollapsed("right"));
  const collapseRail = (v: boolean) => { setRailCollapsed(v); saveCollapsed("left", v); };
  const collapsePages = (v: boolean) => { setPagesCollapsed(v); saveCollapsed("right", v); };

  // The pages panel is DRAGGABLE — a document panel whose width is the reader's call.
  const [pagesW, setPagesW] = useState<number>(() => {
    const n = Number(localStorage.getItem("vexa.minutes.pagesW"));
    const cap = maxPagesW(window.innerWidth);
    // CLAMP a stored width, never discard it: a width chosen on a wide monitor should come back as
    // the widest this window allows, not silently reset to the default the next time you open a laptop.
    return Number.isFinite(n) && n >= T.pagesMin ? Math.min(n, cap) : Math.min(T.pagesDefault, cap);
  });
  const dragging = useRef(false);
  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!dragging.current) return;
      e.preventDefault();
      const cap = maxPagesW(window.innerWidth);
      setPagesW(Math.min(cap, Math.max(T.pagesMin, window.innerWidth - e.clientX)));
    };
    const up = () => {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.style.cursor = ""; document.body.style.userSelect = "";
      setPagesW((w) => { try { localStorage.setItem("vexa.minutes.pagesW", String(w)); } catch { /* ignore */ } return w; });
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
  }, []);
  useEffect(() => {
    const onResize = () => setPagesW((w) => Math.min(maxPagesW(window.innerWidth), Math.max(T.pagesMin, w)));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const startDrag = () => { dragging.current = true; document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none"; };
  const nudge = (d: number) => setPagesW((w) => {
    const n = Math.min(maxPagesW(window.innerWidth), Math.max(T.pagesMin, w + d));
    try { localStorage.setItem("vexa.minutes.pagesW", String(n)); } catch { /* ignore */ }
    return n;
  });

  const mountSet = useCallback(async (wanted: string[]) => {
    // Mount every shared workspace in the chat's set; park the rest. personal/_global/_system
    // ride along by construction. Best-effort — the chat runs regardless.
    try {
      const share = wanted.filter((w) => w !== "personal" && w !== "_global");
      for (const w of share) await setSharedActive(w, true).catch(() => undefined);
      const act = await readActiveSet().catch(() => null);
      for (const m of act?.active ?? []) if (m.role === "shared" && !share.includes(m.slug)) { try { await deactivateWorkspace(m.slug); } catch { /* parked */ } }
    } catch { /* best-effort */ }
  }, []);

  /** Open a chat. The ONE place a room's artifacts land.
   *
   *  A chat that has been read before REOPENS ITS OWN TABS — `artifacts[]` and `focus` are saved on
   *  the record, so leaving a conversation and coming back finds the same documents. A fresh chat
   *  starts from what the ROOM offers: the meeting's phase pages (prep vs has-transcript), or a
   *  README per mounted workspace. A pending `?view=` then seeds which artifact opens, exactly once.
   *
   *  Every setState below runs after the single `await`, so React commits them together — which is
   *  what lets the artifacts effect trust that `sel.chatId` and `pages` describe the same chat. */
  const openChat = useCallback(async (c: ChatRec) => {
    const m = c.meeting ? meetings.find((x) => String(x.id) === c.meeting) : undefined;
    await mountSet(c.workspaces);
    const roomPages = async (): Promise<Page[]> => {
      if (c.meeting) {
        // TWO layouts, keyed on whether a transcript exists yet (founder ruling): prep opens the
        // brief; live and post both lead with the transcript. `meetingPhase()` still returns three —
        // chat.tsx needs them for its mode chip — but live/post render the same room here.
        // It is a property of the MEETING, never of the link that opened it: an emailed link is
        // clicked at an unpredictable time, so a "prep" link followed after the meeting must not lie.
        // A `?mock=1` meeting has no row behind it, so its transcript stays the canned markdown
        // page; every real meeting gets the canvas, bound to its row id.
        const fake = mock && MOCK_MEETINGS.some((x) => String(x.id) === c.meeting);
        return pagesForPhase(m ? meetingPhase(m) : "post", (m as { native_id?: string } | undefined)?.native_id,
          fake ? null : c.meeting);
      }
      // THE DESK IS THE DEFAULT PAGE (PRD decision 26.4). This used to open the ORGANISATION's
      // README for a chat with no focus — the company's document, the same for everybody, and not
      // what a person opening a fresh conversation is looking at their screen to find out. And the
      // tabs are NAMED from the registry rather than by slug: this strip read `126` (F49).
      return deskPanelPages(c.workspaces);
    };
    // THE CHAT'S HOME LEADS THE STRIP (decision 28.5). Composed here rather than stored, so it
    // follows the chat's mounts if they change and can never be `×`-ed away — it is the product's
    // first entry, not something the reader put there.
    const base: Page[] = withHome(
      (c.artifacts.length ? c.artifacts.map((a) => ({ ...a })) : await roomPages()) as Artifact[],
      c.workspaces,
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
    const stored = c.view ? { kind: c.view.kind, path: c.view.path, slug: c.view.slug, label: c.view.label } as Page : null;
    const front = stored ?? focus ?? list[0];
    setSel({
      kind: c.meeting ? "meeting" : "chat",
      chatId: c.id,
      meetingId: c.meeting,
      label: c.label || (m ? meetingTitle(m) : "Chat"),
      workspaces: c.workspaces,
    });
    setPages(list);
    setListing(null);
    if (front) { setDocPath(front.path); setDocSlug(front.slug); setDocKind(front.kind === "meeting" ? "meeting" : "doc"); }
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
  const openRow = useCallback(async (r: Row, opts: { touched?: boolean; artifacts?: Artifact[]; focus?: string } = {}) => {
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
    await openChat(want);
    return want.id;
  }, [meetings, openChat, persist]);

  const openMeeting = useCallback(async (m: MeetingMock, opts: { touched?: boolean; artifacts?: Artifact[]; focus?: string } = {}) => {
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

  /** `+` — open a chat that IS NOT A RECORD YET (F35). Nothing is written; the rail gains no row
   *  (the draft is not in `allChats`); the panel opens on the room's own pages exactly as it would
   *  for a stored chat. It becomes real at the first human turn and not one moment earlier. */
  const startDraft = useCallback(() => {
    const c = newChat("New chat", ["personal", "_global"], { touched: false });
    setDraft(c);
    void openChat(c);
  }, [openChat]);

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

  /** Fire a proposal chip. The founder chose IMMEDIATE, for consistency with the emailed links:
   *  a click sends the turn, with nothing left in the composer to press Enter on. And it sends it
   *  HERE — **a chip acts in the chat it renders in and never mints a row** (founder, 2026-09-01:
   *  he pressed one inside a chat he had just created and got a second one — "clicking this button
   *  should not create a new chat, this chat is already new").
   *
   *  What the click does to the record is `applyProposal`, which is pure and tested; this is the
   *  wiring only — persist it, re-lay-out if the chat just became a meeting's, say the line into the
   *  SAME session. A meeting chip therefore REBINDS: the conversation in front takes the meeting's
   *  ref and title, and the phase pages open beside it exactly as they do from the rail. */
  const runProposal = async (p: Proposal) => {
    // …including inside a DRAFT: the chat in front is the draft when there is one, so a chip
    // PROMOTES it rather than minting a second row beside it. That is the founder's 2026-09-01
    // ruling ("this chat is already new") applied to F35's unwritten chat.
    const current = chatsRef.current.find((c) => c.id === sel.chatId) ?? draftRef.current ?? null;
    const eff = applyProposal(p, current, meetings);
    if (!eff) return;
    // The offer is spent the moment it is taken. A kick is hidden AND settle-delayed, so without
    // this the row would sit there for another 1.2 seconds, live, offering to do the same thing.
    setSpent(sel.chatId);
    if (eff.act === "filter") { toggleAll(true); return; }
    if (eff.act === "create") { addChat(eff.label, ["personal", "_global"], { kick: eff.kick, say: eff.say }); return; }
    if (eff.act === "open") {
      const m = meetings.find((x) => String(x.id) === eff.meetingId);
      const chatId = m ? await openMeeting(m, { touched: true }) : null;
      if (!chatId) return;
      setSpent(chatId);
      if (eff.kick) fireKick(chatId, eff.kick, eff.say);
      return;
    }
    // The chat in front, mutated in place. It only needs re-opening when it CHANGED ROOM — a plain
    // relabel must not throw away the pages panel the reader is looking at.
    const rebound = eff.chat.meeting !== current?.meeting;
    persist((prev) => upsertChat(prev, eff.chat));
    setDraft((d) => (d && d.id === eff.chat.id ? null : d));
    if (rebound) await openChat(eff.chat);
    else setSel((x) => ({ ...x, label: eff.chat.label }));
    if (eff.kick) fireKick(eff.chat.id, eff.kick, eff.say);
  };

  /** Up to three chips, recomputed from the two lists already in hand plus one marker. Pure, so
   *  the row is decided in the render that draws it — no fetch, no model call, no effect. */
  const chips = useMemo(() => proposals(meetings, allChats, scaffolded, Date.now(), email), [meetings, allChats, scaffolded, email]);
  /** Which chat has already spent its offer — see `runProposal`. Per chat, because a different
   *  conversation has not been offered anything yet. */
  const [spent, setSpent] = useState<string | null>(null);
  const shownChips = spent === sel.chatId ? [] : chips;

  // Deleting a chat drops it from the rail (its agent session stays on the server — the row is the
  // user's index, not the record). A meeting's row comes back as a derived row, because the meeting
  // itself did not go anywhere.
  const deleteChat = (chatId: string) => {
    persist((prev) => removeChat(prev, chatId));
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
      const same = c.focus === focus && c.artifacts.length === pages.length
        && artifactKey(c.view ?? { path: "" }) === artifactKey(view)
        && c.artifacts.every((a, k) => artifactKey(a) === artifactKey(pages[k]) && a.label === pages[k].label);
      if (same) return prev;
      const next = [...prev];
      next[i] = { ...c, artifacts: pages.map((pg) => ({ kind: pg.kind, path: pg.path, slug: pg.slug, label: pg.label, pinned: true })), focus, view };
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

  const openPage = useCallback((pg: Page) => {
    const e: Artifact = { kind: pg.kind, path: pg.path, slug: pg.slug, label: pg.label };
    // What this person actually opens is the desk README's ordering signal — and the only place
    // that knows it is here. Fire-and-forget; a usage signal is never worth a millisecond of the
    // document they asked for. (The seam worker's panel-view-slot lands the same one line.)
    if (pg.kind !== "meeting") reportOpened(pg.slug, pg.path);
    // A folded-away panel is the other way a link click "does nothing": the tab opens into a 22px
    // column nobody can see. Asking for a document unfolds the column it lands in.
    setPagesCollapsed(false); saveCollapsed("right", false);
    // standard history semantics: navigating after going BACK truncates the forward branch, and
    // re-opening the document already in front is not a navigation at all.
    setHist((h) => {
      const cur = h.stack[h.i];
      if (cur && artifactKey(cur) === artifactKey(e)) return h;
      const stack = [...h.stack.slice(0, h.i + 1), e];
      return { stack, i: stack.length - 1 };
    });
    // ONE VIEW SLOT, AND THE STRIP IS ITS HISTORY (PRD decision 28 + the founder's amendment).
    // Navigating replaces what the panel shows AND records where you were: `touchHistory` dedups by
    // identity, moves the page to the RIGHT end beside the current one, and caps at 12 by evicting
    // the oldest UNPINNED entry. Pins sit at the left edge and never age out.
    setPages((prev) => touchHistory(prev, { kind: pg.kind, path: pg.path, slug: pg.slug, label: pg.label }, Date.now()));
    setDocPath(pg.path); setDocSlug(pg.slug); setDocKind(pg.kind === "meeting" ? "meeting" : "doc");
    setListing(null); setDocNonce((n) => n + 1);
  }, []);

  /** PIN A PAGE. The amendment folded "open in tab" into this: the strip is history, so everything
   *  you open is already in it, and the only extra thing worth asking for is that one STAYS. */
  const openPinned = useCallback((pg: Page) => {
    openPage(pg);
    setPages((prev) => prev.map((x) => artifactKey(x) === artifactKey(pg) ? { ...x, pinned: true } : x));
  }, [openPage]);

  /** Pin what is in front, or unpin it. The pin is the whole of "specifically requested" for a
   *  document the reader navigated to and then decided to keep. */
  const pinned = useMemo(
    () => pages.some((x) => artifactKey(x) === artifactKey({ kind: docKind, path: docPath, slug: docSlug })),
    [pages, docKind, docPath, docSlug],
  );
  const togglePin = useCallback(() => {
    const key = artifactKey({ kind: docKind, path: docPath, slug: docSlug });
    setPages((prev) => {
      const hit = prev.find((x) => artifactKey(x) === key);
      if (hit) return prev.filter((x) => artifactKey(x) !== key);
      const label = (docPath.split("/").pop() || docPath).replace(/\.md$/i, "");
      return [...prev, { kind: docKind === "meeting" ? "meeting" as const : undefined, path: docPath, slug: docSlug, label, pinned: true }];
    });
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
      openPage({ path: d.path, slug: d.workspace, label: d.label });
    };
    window.addEventListener(VIEW_NAVIGATE_EVENT, onView);
    return () => window.removeEventListener(VIEW_NAVIGATE_EVENT, onView);
  }, [openPage]);

  /** Walk the stack without disturbing it. A document closed since it was visited is REOPENED as a
   *  tab — going back to somewhere you have been should never fail because you tidied up. */
  const step = (delta: number) => {
    const j = hist.i + delta;
    if (j < 0 || j >= hist.stack.length) return;
    const e = hist.stack[j];
    setHist({ ...hist, i: j });
    setPages((prev) => prev.some((x) => artifactKey(x) === artifactKey(e)) ? prev : [...prev, { kind: e.kind, path: e.path, slug: e.slug, label: e.label }]);
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
    const next = forgetHistory(pages, key);
    setPages(next);
    if (key === artifactKey({ kind: docKind, path: docPath, slug: docSlug }) && next.length) {
      openPage(next[Math.min(i, next.length - 1)]);
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
  const navigate = useCallback(async (slug: string | undefined, prefix: string) => {
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
    setListing({ slug, prefix, dirs: [...dirs].sort(), files: [...leaves].sort() });
  }, []);

  /** The chat's focus set, edited from the header. The mount set follows immediately — the point of
   *  changing it is the next turn, not the next reload. */
  const setWorkspaces = (fn: (ws: string[]) => string[]) => {
    const id = sel.chatId, next = fn(sel.workspaces);
    setSel((x) => ({ ...x, workspaces: next }));
    persist((prev) => prev.map((c) => (c.id === id ? { ...c, workspaces: next } : c)));
    // A DRAFT is not in the stored list, so the mount set has to land on the record that IS in
    // front — otherwise it would be right for this sitting and lost the moment the draft promotes.
    setDraft((d) => (d && d.id === id ? { ...d, workspaces: next } : d));
    void mountSet(next);
  };

  useEffect(() => {
    let dead = false;
    // a meeting tab has no body to fetch: the canvas fetches its own transcript by row id.
    if (docKind === "meeting") { setDocBody(null); return; }
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
      const d = (e as CustomEvent<{ path?: string; wikilink?: string; slug?: string; docPath?: string }>).detail || {};
      const r = await resolveDocRef(d, { path: d.docPath, slug: d.slug }).catch(() => null);
      // NEVER A DEAD CLICK: an unresolved link opens its canonical path anyway, so the panel
      // answers with the empty state instead of the click vanishing (pageForDocRef).
      const pg = pageForDocRef(d, r);
      if (pg) openPage(pg);
    };
    const onMeeting = (e: Event) => {
      const ref = (e as CustomEvent<{ ref?: string }>).detail?.ref;
      if (!ref) return;
      const native = ref.includes("/") ? ref.slice(ref.indexOf("/") + 1) : ref;
      const m = meetings.find((x) => (x as { native_id?: string }).native_id === native || String(x.id) === ref);
      // same rule: a ref with no row behind it opens the meeting's notes page rather than nothing.
      if (m) void openMeeting(m); else openPage(pageForMeetingRef(ref));
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

  // ── F41: A FILE THE TURN WROTE BECOMES A TAB ────────────────────────────────────────────────
  //
  //  The founder created a shared workspace, the agent wrote its README, and the right panel stayed
  //  on `_global/README.md` — the one document the turn had just made was the one thing not on
  //  screen.
  //
  //  Three rules, and the third is the one worth stating:
  //    · the tab goes on the CHAT RECORD, not on panel-local state. Layout is a function of the
  //      chat's state (decision 18), so a reload shows the same tabs — which is why this appends to
  //      `pages` and lets the artifacts effect, the record's ONE writer, persist it.
  //    · it comes to the FRONT only when the event says `focus: true`.
  //    · …and never over a focus the READER chose. A person who has opened something is reading it;
  //      an agent's write appears in the strip and waits. The tab still appears — being appended is
  //      not conditional on anything.
  //  Appending is idempotent by artifact key, so the same file written twice in a turn is one tab.
  //  The decision itself is `artifactTabEffect` — pure, in roomView.ts, tested there. This is the
  //  wiring: read the tabs in hand, apply it, and either bring the page forward through the ONE
  //  route into the panel (`openPage`, which also unfolds a collapsed column and pushes history) or
  //  append it quietly behind the reader.
  const pagesRef = useRef(pages);
  useEffect(() => { pagesRef.current = pages; }, [pages]);
  useEffect(() => {
    const onArtifact = (e: Event) => {
      const detail = (e as CustomEvent<{ workspace?: string; path?: string; focus?: boolean }>).detail || {};
      const eff = artifactViewEffect(detail, readerChoseFocus.current);
      if (!eff) return;              // `focus: false` is now NOTHING VISIBLE, not a quiet tab
      openPage(eff.view);
    };
    window.addEventListener(ARTIFACT_EVENT, onArtifact);
    return () => window.removeEventListener(ARTIFACT_EVENT, onArtifact);
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
    try { ref = localStorage.getItem("vexa.openMeetingRef"); localStorage.removeItem("vexa.openMeetingRef"); }
    catch { return; }
    if (!ref) return;
    const native = ref.includes("/") ? ref.slice(ref.indexOf("/") + 1) : ref;
    const m = meetings.find((x) => (x as { native_id?: string }).native_id === native || String(x.id) === ref);
    if (m) void openMeeting(m, { touched: true });
  }, [meetings, openMeeting]);

  const session = sel.chatId;

  // The header says which phase the room is in. It read isHeld() — a two-way test — so a LIVE
  // meeting announced itself as "upcoming" beside a transcript that was visibly filling. Three
  // states now, in the meeting's own vocabulary.
  const PHASE_WORD = { prep: "upcoming", live: "live", post: "held" } as const;
  const selMeeting = sel.kind === "meeting" ? meetings.find((m) => String(m.id) === sel.meetingId) : undefined;
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
  const flavor = sel.kind === "meeting" ? `meeting · ${selMeeting ? PHASE_WORD[meetingPhase(selMeeting)] : "held"}`
    : selChat?.scaffold?.kind === "admin-setup" ? "chat · admin" : "chat";

  // PRD DECISION 30 — THE SURFACE IS A FACT THE SERVER HOLDS, not something the prompt re-describes.
  // Every change to what the human is looking at is written to the session record: which chat, which
  // meeting and phase, the view, the strip's history and pins, the navigator. Debounced and
  // fire-and-forget inside `syncSurface`; inert until stage-1's route lands, and the prompt keeps
  // its "Active context" prefix until the same flag flips.
  useEffect(() => {
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
      navigator: { open: !pagesCollapsed, workspace: docSlug ?? null },
    });
  }, [sel.chatId, sel.kind, sel.meetingId, selMeeting, pages, docPath, docSlug, pagesCollapsed]);


  // `?ask=<preset>` — the emailed link. App.tsx stashed the name; resolve it to an ADMIN-AUTHORED
  // body in `_global/asks/<name>.md` and open a fresh chat already holding it. The preset also says
  // which workspaces the chat is over, so context and opening prompt arrive together — which is the
  // whole point of the link. Editing the file changes every future click; nothing is rebuilt.
  // The chat is created TOUCHED: today nothing distinguishes a link the user clicked from one a
  // flow injected, and the safe reading of an ambiguous case is "the human meant to be here".
  const presetFired = useRef(false);
  const meetingsLoaded = useLiveMeetingsLoaded();
  // A bounded wait, so a meetings list that never answers cannot leave the click with nothing: the
  // preset fires anyway after this, naming the meeting less well rather than not at all.
  const [presetWaited, setPresetWaited] = useState(false);
  const presetTimer = useRef(false);

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
      kick: sc.openingText,
      // The record travels ONTO the chat — kind and id together (F37). It never used to: the kind
      // was computed here and dropped on the floor, which is why the header could only fall back to
      // mount arithmetic and why a planted row could wear the admin flavour without a record.
      scaffold: rec.scaffold,
      scaffoldId: sc.kind === "hand-link" ? undefined : sc.id,
    });
  }, [addChat]);

  // A scaffold that would not open. Held so the reader is TOLD — a person who clicked a real link
  // and landed on a blank chat cannot tell a spent invitation from a broken product.
  const [scaffoldRefusal, setScaffoldRefusal] = useState<ScaffoldRefusal | null>(null);
  const scaffoldFired = useRef(false);

  // WHO THE SERVER THINKS IS ASKING (F48). A refusal is a judgement about an identity, and the
  // reader cannot see which identity that was — so the card names it. Probed only when a refusal
  // actually exists: the common path is a link that opens, and it should cost nothing. Fail-soft —
  // an identity probe that does not answer leaves the copy exactly as it read before.
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
  // chat record only — the decision-18 contract — so this effect's whole job is to write that
  // record correctly and then get out of the way.
  useEffect(() => {
    if (scaffoldFired.current) return;
    let id: string | null = null;
    try { id = localStorage.getItem("vexa.pendingScaffold"); } catch { /* ignore */ }
    if (!id) return;
    // NO WAIT ON THE MEETINGS LIST. The record carries `native` itself, so nothing here needs the
    // list to resolve the note tab — which matters because the list is exactly what may not have
    // loaded yet when an emailed link lands, and the preset path's 8s wait exists only because it
    // had to hunt for that id. One record, and it is complete.
    scaffoldFired.current = true;
    // (The scaffold used to CLAIM the opening here — `setPresetInFlight(true)` — so the cached
    // greeting would stand down. There is no greeting left to race: F36 deleted it.)
    try { localStorage.removeItem("vexa.pendingScaffold"); } catch { /* ignore */ }
    void (async () => {
      const got = await fetchScaffold(id);
      if (!got.ok) {
        console.error("scaffold " + id + " did not open:", got.refusal.reason, got.refusal.detail);
        setScaffoldRefusal(got.refusal);
        return;
      }
      openFromScaffold(got.scaffold);
    })();
  }, [addChat, meetings, meetingsLoaded, presetWaited]);

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
    // (The preset used to CLAIM the opening here, synchronously, so the cached greeting fired at
    // +600ms would stand down — a brand-new attendee who clicked a minutes link otherwise got "I'm
    // booked for your meeting" about a meeting that had already happened. F36 deleted the greeting,
    // so there is nothing left to claim it from.)
    // A preset ABOUT a meeting waits for the meeting list: substituting before it lands is what
    // put a Zoom number where the meeting's NAME belongs. A preset with no ref waits for nothing.
    if (intent.meeting && !meetingsLoaded && !presetWaited) {
      if (!presetTimer.current) {
        presetTimer.current = true;
        window.setTimeout(() => setPresetWaited(true), 8000);
      }
      return;
    }
    presetFired.current = true;
    try { localStorage.removeItem("vexa.pendingPreset"); } catch { /* ignore */ }
    void (async () => {
      const body = await readWorkspaceFile("asks/" + name + ".md", { slug: "_global" })
        .catch((e) => { console.error("preset asks/" + name + ".md could not be read:", e); return null; });
      // An unknown preset opens nothing — but never SILENTLY. A click that produces nothing is
      // indistinguishable from a broken product, so it is logged and the opening is handed back to
      // the greeting this preset had suppressed.
      if (!body || !body.trim()) {
        console.error("preset asks/" + name + ".md is missing or empty — the emailed link opened nothing");
        // The opening is handed back to the COMPOSER: there is no cached greeting to fall back to
        // any more (F36 deleted the seed). An empty composer is what an unresolved link honestly
        // leaves behind.
        return;
      }
      // Optional frontmatter: `mounts:` and `label:`, plus `tabs:`/`focus:` — the preset's own
      // statement of WHICH DOCUMENTS its conversation is about, and which one is in front. PRD
      // decision 18: the link sets the chat's record and the panel renders only from the record, so
      // this is where a link stops being just an opening sentence and becomes a room.
      let text = body, mounts: string[] = [], label = name.replace(/[-_]/g, " ");
      let tabTokens: string[] = [], focusToken = "";
      const fm = /^---\n([\s\S]*?)\n---\n?/.exec(body);
      if (fm) {
        text = body.slice(fm[0].length);
        const mm = /^mounts:\s*(.+)$/m.exec(fm[1]);
        if (mm) mounts = mm[1].split(",").map((x) => x.trim()).filter(Boolean);
        const l = /^label:\s*(.+)$/m.exec(fm[1]);
        if (l) label = l[1].trim();
        const tt = /^tabs:\s*(.+)$/m.exec(fm[1]);
        if (tt) tabTokens = tt[1].split(",").map((x) => x.trim()).filter(Boolean);
        const ff = /^focus:\s*(.+)$/m.exec(fm[1]);
        if (ff) focusToken = ff[1].trim();
      }
      if (intent.ws) mounts = [intent.ws, ...mounts.filter((x) => x !== intent.ws)];
      if (!mounts.length) mounts = ["_global", "personal"];
      // The ROW behind the ref, so a preset can name the meeting instead of reciting its id.
      const ref = (intent.meeting || "").trim();
      const nativeRef = ref.includes("/") ? ref.slice(ref.indexOf("/") + 1) : ref;
      const row = ref
        ? meetings.find((x) => String(x.id) === ref || (x as { native_id?: string }).native_id === nativeRef)
        : undefined;
      // `{{state}}` — WHO IS THIS, ROUGHLY, so a preset can branch between a first contact and a
      // returning one without the agent having to guess from an empty workspace. Two axes, both
      // read off state the client already holds, both deliberately coarse:
      //   personal:new   this is their FIRST chat on this Vexa — a stranger who clicked a mail
      //   personal:warm  they have been here before
      //   group:absent   the meeting is bound to no shared workspace
      //   group:new      bound, but no other meeting in the list shares that binding
      //   group:warm     bound, with history behind it
      // The preset branches on the STRING, in prose. `_global` is not an axis: once the company
      // layer's gate holds, it is always present.
      const wsId = (row as { workspace_id?: string } | undefined)?.workspace_id || "";
      const groupState = !wsId
        ? "absent"
        : meetings.some((x) => (x as { workspace_id?: string }).workspace_id === wsId && String(x.id) !== String(row?.id))
          ? "warm" : "new";
      const stateToken = `personal:${chatsRef.current.length ? "warm" : "new"} group:${groupState}`;
      const prompt = text
        // `{{workspace}}` — what a person's own workspace is CALLED to them. One constant
        // (minutes/vocabulary.ts), so the rename the founder has not made yet is one edit and not a
        // sweep through every preset.
        .replace(/\{\{\s*workspace\s*\}\}/g, WORKSPACE_WORD)
        .replace(/\{\{\s*state\s*\}\}/g, stateToken)
        .replace(/\{\{\s*meeting\s*\}\}/g, ref || "the meeting in view")
        .replace(/\{\{\s*title\s*\}\}/g, row?.title || "the meeting in view")
        .replace(/\{\{\s*when\s*\}\}/g, row?.when || "")
        .replace(/\{\{\s*ws\s*\}\}/g, mounts[0] || "")
        .replace(/\{\{\s*today\s*\}\}/g, new Date().toISOString().slice(0, 10))
        .trim();
      if (!prompt) return;
      // ONE consumer for a link that carries BOTH `?ask=` and `?meeting=`. There used to be two:
      // this effect opened an askchat and selected it, the `?meeting=` effect then opened the
      // meeting's own chat over the top, and the kick fired 1.2s later into a session no longer
      // mounted — so an attendee got the meeting room with its cached phase greeting and never the
      // preset at all. When the ref resolves, the preset speaks INTO the meeting's chat and spends
      // the meeting ref here, so nothing re-opens it underneath.
      // THE LINK'S ROOM. Resolve the preset's `tabs:`/`focus:` against the meeting it names, so
      // `meeting:note` becomes the Brief before the meeting and the Minutes after it, and
      // `_global/README.md` becomes a real tab on the org tier. A meeting chat with NO declared
      // tabs keeps the phase layout (openChat's roomPages) — the rule, not the link, decides then.
      const phase = row ? meetingPhase(row) : null;
      const tabCtx = {
        native: (row as { native_id?: string } | undefined)?.native_id ?? null,
        meetingId: row ? String(row.id) : null,
        phase,
        mounts,
      };
      const artifacts = artifactsFromTokens(tabTokens, tabCtx);
      const focusArt = focusToken ? artifactsFromTokens([focusToken], tabCtx)[0] : undefined;
      const focusKey = focusArt ? artifactKey(focusArt) : undefined;

      // A HAND LINK MINTS A LOCAL SCAFFOLD and renders through the one path (PRD §5.5 step 3).
      // `?ask=&meeting=` survives only as this fallback: it composes the SAME record an emailed
      // `?s=` produces, so there is one composer rather than two that drift. Nothing about the URL
      // carries prompt text — the body still comes from `_global/asks/<name>.md`, admin-authored.
      if (row) {
        try { localStorage.removeItem("vexa.openMeetingRef"); } catch { /* ignore */ }
        meetingRefSpent.current = true;
      }
      openFromScaffold(localScaffold({
        preset: name,
        openingText: prompt,
        meeting: row ? String(row.id) : (/^\d+$/.test(ref) ? ref : null),
        native: tabCtx.native,
        phase,
        workspaces: mounts,
        tabs: tabTokens,
        focus: focusToken,
        title: row?.title,
      }));
      return;
    })();
  }, [openFromScaffold, meetings, meetingsLoaded, presetWaited]);

  return (
    <div style={{ position: "relative", display: "grid", gridTemplateColumns: `${railCollapsed ? EDGE_W : T.railW}px minmax(0, 1fr) ${pagesCollapsed ? EDGE_W : pagesW}px`, gridTemplateRows: `${T.headerH}px 1fr`, height: "100%", minHeight: 0, background: surface.rail }}>
      {railCollapsed
        ? <EdgeHandle side="left" onClick={() => collapseRail(false)} />
        : <Rail rows={shownRows} hidden={hiddenCount} all={all} onAll={toggleAll}
            selKey={selKey} onSelect={(r) => void openRow(r)}
            onNewChat={startDraft} onDeleteChat={deleteChat}
            onCollapse={() => collapseRail(true)} />}
      <ContextBar sel={sel} flavor={flavor} memberships={memberships}
        onAddWorkspace={(id) => setWorkspaces((ws) => ws.includes(id) ? ws : [...ws, id])}
        onRemoveWorkspace={(id) => setWorkspaces((ws) => ws.filter((w) => w !== id))}
        onAttachRepo={(id) => setAttachTo({ id })} />
      {/* Wrapped rather than a bare id so "the desk" (id undefined) is still an OPEN dialog — a
          nullable id alone cannot tell "no dialog" from "dialog, aimed at the desk". */}
      {attachTo && (
        <AttachRepo workspaceId={attachTo.id} onClose={() => setAttachTo(null)}
          onAttached={(id) => { if (id) setWorkspaces((ws) => ws.includes(id) ? ws : [...ws, id]); }} />
      )}
      <main style={{ gridRow: 2, gridColumn: 2, minWidth: 0, minHeight: 0, background: surface.center, display: "flex", flexDirection: "column" }}>
        {/* A SCAFFOLD THAT WOULD NOT OPEN STATES ITSELF. Someone who clicked a real link and landed
            on an empty conversation cannot tell a spent invitation from a broken product — and the
            second reading is the one they take. So: whose it is, and what to do about it. It sits
            ABOVE the chat rather than replacing it, because their own conversations are still
            theirs and hiding them would be a second wrong. */}
        {scaffoldRefusal && (
          <ScaffoldRefusalCard refusal={scaffoldRefusal} signedInAs={signedInAs}
            onDismiss={() => setScaffoldRefusal(null)} />
        )}
        <div style={{ flex: 1, minHeight: 0 }}>
          <Chat params={{ session }} emptyExtra={<ProposalChips items={shownChips} onPick={(p) => void runProposal(p)} />} />
        </div>
      </main>
      {/* the pages panel's resize handle — a real separator: 11px hit area, a hairline that
          lights up on hover/focus, and arrow keys for anyone not dragging. A collapsed panel has no
          width to drag, so the separator goes with it. */}
      {!pagesCollapsed && <div role="separator" aria-orientation="vertical" aria-label="Resize pages panel" tabIndex={0}
        onMouseDown={startDrag}
        onKeyDown={(e) => { if (e.key === "ArrowLeft") { e.preventDefault(); nudge(24); } if (e.key === "ArrowRight") { e.preventDefault(); nudge(-24); } }}
        onMouseEnter={(e) => { (e.currentTarget.firstElementChild as HTMLElement).style.background = "var(--accent)"; }}
        onMouseLeave={(e) => { if (!dragging.current) (e.currentTarget.firstElementChild as HTMLElement).style.background = "transparent"; }}
        onFocus={(e) => { (e.currentTarget.firstElementChild as HTMLElement).style.background = "var(--accent)"; }}
        onBlur={(e) => { (e.currentTarget.firstElementChild as HTMLElement).style.background = "transparent"; }}
        style={{ position: "absolute", top: 0, bottom: 0, right: pagesW - 5, width: 11, cursor: "col-resize", zIndex: 5, display: "flex", justifyContent: "center", outline: "none" }}>
        <span style={{ width: 1, alignSelf: "stretch", background: "transparent", transition: "background .12s" }} />
      </div>}
      {pagesCollapsed
        ? <EdgeHandle side="right" onClick={() => collapsePages(false)} />
        : <PagesPanel pages={pages} docPath={docPath} docSlug={docSlug} docKind={docKind}
            onTogglePin={togglePin} pinned={pinned} onOpen={(pg) => { readerChoseFocus.current = true; openPage(pg); }} onClose={closeTab}
            listing={listing} onNavigate={(slug, prefix) => void navigate(slug, prefix)}
            canBack={canBack} canForward={canForward} onBack={goBack} onForward={goForward}
            body={docBody} onSaved={() => setDocNonce((n) => n + 1)}
            onCollapse={() => collapsePages(true)} />}
    </div>
  );
}

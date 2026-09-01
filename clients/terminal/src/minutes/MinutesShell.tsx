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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ASK_CHAT_EVENT, CHAT_TOUCHED_EVENT, ONBOARDING_SEED_EVENT, OPEN_ENTITY_EVENT, OPEN_MEETING_EVENT } from "../canvas/actions";
import { Chat } from "../surfaces/chat";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import {
  readActiveSet, setSharedActive, deactivateWorkspace, readWorkspaceFile,
  listSharedMemberships, listWorkspaceTree, type Membership,
} from "../surfaces/workspaceApi";
import { ContextBar } from "./ContextBar";
import { PagesPanel, type Listing } from "./PagesPanel";
import {
  chatForRow, loadChats, loadRailAll, markTouched, meetingTitle, newChat, railRows, removeChat,
  saveChats, saveRailAll, upsertChat, visibleRows, artifactKey, PERSONAL_CHAT_ID,
  type Artifact, type Chat as ChatRec, type Row,
} from "./chats";
import { resolveDocRef } from "../ui-kit/docLinks";
import { Rail } from "./Rail";
import { meetingPhase, type MeetingMock } from "../surfaces/meetingModel";
import { pagesForPhase, resolveView, VIEW_KEY } from "./roomView";
import { proposals, type Proposal } from "./proposals";
import { ProposalChips } from "./ProposalChips";
import { MOCK_CHATS, MOCK_MEETINGS, mockBody, mockOn } from "./mockPhases";
import { T, maxPagesW, surface } from "./tokens";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import type { Page, Sel } from "./types";

const PERSONAL_SEL: Sel = { kind: "chat", chatId: PERSONAL_CHAT_ID, label: "Personal", workspaces: ["personal", "_global"] };

export function MinutesShell() {
  const layout = useService(LayoutServiceId);
  const realMeetings = useLiveMeetings();
  // `?mock=1` — three fabricated meetings, one per phase, plus a handful of never-touched
  // auto-created chats so the rail's FILTER can be judged before the data exists. Off unless the
  // flag is set; see mockPhases.ts.
  const mock = mockOn();
  const mockStart = useRef(Date.now());
  const meetings = useMemo(() => (mock ? [...MOCK_MEETINGS, ...realMeetings] : realMeetings), [mock, realMeetings]);
  // The stored list. Mock chats are merged for DISPLAY only — they are never written back.
  const [chats, setChats] = useState<ChatRec[]>(() => loadChats());
  const allChats = useMemo(() => (mock ? [...chats, ...MOCK_CHATS] : chats), [mock, chats]);
  const chatsRef = useRef(allChats);
  useEffect(() => { chatsRef.current = allChats; }, [allChats]);
  const [all, setAll] = useState<boolean>(() => loadRailAll());
  const [sel, setSel] = useState<Sel>(PERSONAL_SEL);
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
    const roomPages = (): Page[] => {
      if (c.meeting) {
        // TWO layouts, keyed on whether a transcript exists yet (founder ruling): prep opens the
        // brief; live and post both lead with the transcript. `meetingPhase()` still returns three —
        // chat.tsx needs them for its mode chip — but live/post render the same room here.
        // It is a property of the MEETING, never of the link that opened it: an emailed link is
        // clicked at an unpredictable time, so a "prep" link followed after the meeting must not lie.
        return pagesForPhase(m ? meetingPhase(m) : "post", (m as { native_id?: string } | undefined)?.native_id);
      }
      const shared = c.workspaces.filter((w) => w !== "_global");
      if (!shared.length) return [{ path: "README.md", slug: "_global", label: "The organisation" }];
      const ps: Page[] = shared.map((w) => w === "personal"
        ? { path: "README.md", label: "personal" }
        : { path: "README.md", slug: w, label: w });
      ps.push({ path: "README.md", slug: "_global", label: "_global" });
      return ps;
    };
    const base: Page[] = c.artifacts.length ? c.artifacts.map((a) => ({ ...a })) : roomPages();
    let list = base;
    let focus: Page | null = c.focus ? base.find((pg) => artifactKey(pg) === c.focus) ?? null : null;
    if (!viewSpent.current && pendingView) {
      viewSpent.current = true;
      try { localStorage.removeItem(VIEW_KEY); } catch { /* locked-down storage */ }
      const r = resolveView(pendingView, base);
      list = r.pages; focus = r.focus ?? focus;
    }
    const front = focus ?? list[0];
    setSel({
      kind: c.meeting ? "meeting" : "chat",
      chatId: c.id,
      meetingId: c.meeting,
      label: c.label || (m ? meetingTitle(m) : "Chat"),
      workspaces: c.workspaces,
    });
    setPages(list);
    setListing(null);
    if (front) { setDocPath(front.path); setDocSlug(front.slug); }
    setHist(front ? { stack: [{ path: front.path, slug: front.slug, label: front.label }], i: 0 } : { stack: [], i: -1 });
    setDocBody(null); setDocNonce((n) => n + 1);
  }, [meetings, mountSet, pendingView]);

  /** Open a rail row. A row derived from a meeting has no chat yet — first open MATERIALISES one
   *  (id `meet-<meetingId>`, so it lands on the agent session that meeting has always used).
   *  Returns the id of the chat that was actually opened: a caller with something to say to it
   *  (a proposal chip's kick) must address the chat that LANDED, never a reconstructed id. */
  const openRow = useCallback(async (r: Row, opts: { touched?: boolean } = {}) => {
    const existing = r.chatId ? chatsRef.current.find((c) => c.id === r.chatId) : undefined;
    const c = existing ?? chatForRow(chatsRef.current, r, meetings);
    const want = opts.touched ? { ...c, touched: true } : c;
    if (!existing || opts.touched) persist((prev) => upsertChat(prev, want));
    await openChat(want);
    return want.id;
  }, [meetings, openChat, persist]);

  const openMeeting = useCallback(async (m: MeetingMock, opts: { touched?: boolean } = {}) => {
    const id = String(m.id);
    const row = railRows(chatsRef.current, [m]).find((r) => r.meetingId === id);
    return row ? await openRow(row, opts) : null;
  }, [openRow]);

  const addChat = useCallback((label: string, workspaces: string[], opts: { id?: string; kick?: string } = {}) => {
    const c = newChat(label, workspaces, { id: opts.id, touched: true });
    persist((prev) => upsertChat(prev, c));
    void openChat(c);
    if (opts.kick) setTimeout(() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { hidden: true, session: c.id, prompt: opts.kick } })), 1200);
    return c;
  }, [openChat, persist]);

  /** Fire a proposal chip. The founder chose IMMEDIATE, for consistency with the emailed links:
   *  a click opens or creates the chat and sends the turn — nothing is left in the composer to
   *  press Enter on.
   *
   *  None of this is new machinery. A meeting chip is the rail's own meeting-open path plus the
   *  same settle-delayed, session-targeted kick `addChat` uses; `review` is the rail's filter chip;
   *  `setup` is the personal onboarding seed; `group` is a new chat with an opening line. */
  const runProposal = async (p: Proposal) => {
    if (p.kind === "review") { toggleAll(true); return; }
    if (p.kind === "setup") {
      const c = chatsRef.current.find((x) => x.id === PERSONAL_CHAT_ID);
      if (c) await openChat(c);
      setTimeout(() => window.dispatchEvent(new CustomEvent(ONBOARDING_SEED_EVENT)), 400);
      return;
    }
    if (p.kind === "group") { addChat("Daily meetings", ["personal", "_global"], { kick: p.kick }); return; }
    const m = meetings.find((x) => String(x.id) === p.meetingId);
    if (!m) return;
    const chatId = await openMeeting(m, { touched: true });
    if (!chatId || !p.kick) return;
    const kick = p.kick;
    setTimeout(() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { hidden: true, session: chatId, prompt: kick } })), 1200);
  };

  /** Up to three chips, recomputed from the two lists already in hand plus one marker. Pure, so
   *  the row is decided in the render that draws it — no fetch, no model call, no effect. */
  const chips = useMemo(() => proposals(meetings, allChats, scaffolded), [meetings, allChats, scaffolded]);

  // Deleting a chat drops it from the rail (its agent session stays on the server — the row is the
  // user's index, not the record). A meeting's row comes back as a derived row, because the meeting
  // itself did not go anywhere.
  const deleteChat = (chatId: string) => {
    persist((prev) => removeChat(prev, chatId));
    if (sel.chatId === chatId) setSel(PERSONAL_SEL);
  };

  // The tab strip IS the chat's `artifacts[]`, and this effect is its ONE writer. Persisting here
  // rather than at each call site is what makes that true: `openChat` commits `sel.chatId` and
  // `pages` together, so this can never write one chat's tabs onto another. A mock chat is not in
  // the stored list, so it simply finds no row and writes nothing.
  useEffect(() => {
    if (!pages.length) return;
    const id = sel.chatId;
    const focus = artifactKey({ path: docPath, slug: docSlug });
    persist((prev) => {
      const i = prev.findIndex((c) => c.id === id);
      if (i < 0) return prev;
      const c = prev[i];
      const same = c.focus === focus && c.artifacts.length === pages.length
        && c.artifacts.every((a, k) => artifactKey(a) === artifactKey(pages[k]) && a.label === pages[k].label);
      if (same) return prev;
      const next = [...prev];
      next[i] = { ...c, artifacts: pages.map((pg) => ({ path: pg.path, slug: pg.slug, label: pg.label })), focus };
      return next;
    });
  }, [sel.chatId, pages, docPath, docSlug, persist]);

  // The agent should see what the human is reading. chat.tsx builds its context bundle from the
  // layout store's active tab (chatContext.focusTarget maps a `doc` tab to `{kind:"file", ref:
  // "@file:<path>"}`), and in minutes mode nothing ever set it — so `focus` went out null on every
  // turn while a document sat open beside the conversation. The workbench never mounts beside this
  // shell, so the store keeps exactly one writer. Only `path` reaches the wire today; `tabs` rides
  // along for the server to pick up when it wants the whole open set.
  useEffect(() => {
    layout.setActiveTab({ kind: "doc", params: { path: docPath, slug: docSlug, tabs: pages.map((pg) => pg.path) } });
  }, [layout, docPath, docSlug, pages]);
  useEffect(() => () => layout.setActiveTab(null), [layout]);

  // Open a chat on mount. Without this the shell sat on a HARD-CODED selection until the first
  // click: the header advertised `personal` whatever the chat's real focus set was, and the panel
  // opened with no tabs at all. A pending deeplink owns the first room, so this yields to one.
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current || deeplinkPending) return;
    booted.current = true;
    const c = chatsRef.current.find((x) => x.id === PERSONAL_CHAT_ID) ?? chatsRef.current[0];
    if (c) void openChat(c);
  }, [deeplinkPending, openChat]);

  // A user-authored send is the whole definition of "touched" — the cheap flag the default filter
  // reads instead of fetching every chat's history. chat.tsx fires this with its session id, which
  // IS the chat id.
  useEffect(() => {
    const onTouched = (e: Event) => {
      const id = (e as CustomEvent<{ session?: string }>).detail?.session;
      if (id) persist((prev) => markTouched(prev, id));
    };
    window.addEventListener(CHAT_TOUCHED_EVENT, onTouched);
    return () => window.removeEventListener(CHAT_TOUCHED_EVENT, onTouched);
  }, [persist]);

  /** Open a document as a TAB: already open → just focus it; new → append and focus. Every route
   *  into the panel goes through here (entity link, breadcrumb listing, phase page), which is why
   *  the tab set can be trusted as the record of what has been looked at. */
  const openPage = useCallback((pg: Page) => {
    const e: Artifact = { path: pg.path, slug: pg.slug, label: pg.label };
    // standard history semantics: navigating after going BACK truncates the forward branch, and
    // re-opening the document already in front is not a navigation at all.
    setHist((h) => {
      const cur = h.stack[h.i];
      if (cur && artifactKey(cur) === artifactKey(e)) return h;
      const stack = [...h.stack.slice(0, h.i + 1), e];
      return { stack, i: stack.length - 1 };
    });
    setPages((prev) => prev.some((x) => artifactKey(x) === artifactKey(pg)) ? prev : [...prev, pg]);
    setDocPath(pg.path); setDocSlug(pg.slug); setListing(null); setDocNonce((n) => n + 1);
  }, []);

  /** Walk the stack without disturbing it. A document closed since it was visited is REOPENED as a
   *  tab — going back to somewhere you have been should never fail because you tidied up. */
  const step = (delta: number) => {
    const j = hist.i + delta;
    if (j < 0 || j >= hist.stack.length) return;
    const e = hist.stack[j];
    setHist({ ...hist, i: j });
    setPages((prev) => prev.some((x) => artifactKey(x) === artifactKey(e)) ? prev : [...prev, { path: e.path, slug: e.slug, label: e.label }]);
    setDocPath(e.path); setDocSlug(e.slug); setListing(null); setDocNonce((n) => n + 1);
  };
  // A folder listing is not a document, so it is not in the stack — but it IS somewhere you went,
  // and the first ‹ should undo it rather than skipping past the doc you were reading.
  const canBack = !!listing || hist.i > 0;
  const canForward = !listing && hist.i >= 0 && hist.i < hist.stack.length - 1;
  const goBack = () => { if (listing) { setListing(null); return; } step(-1); };
  const goForward = () => step(1);

  /** Close a tab. The last one never closes — an empty panel is not a state worth reaching — and
   *  closing the tab in front hands focus to its neighbour rather than to nothing. */
  const closeTab = (pg: Page) => {
    if (pages.length <= 1) return;
    const key = artifactKey(pg);
    const i = pages.findIndex((x) => artifactKey(x) === key);
    if (i < 0) return;
    const next = pages.filter((_, k) => k !== i);
    setPages(next);
    if (key === artifactKey({ path: docPath, slug: docSlug })) openPage(next[Math.min(i, next.length - 1)]);
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
    void mountSet(next);
  };

  useEffect(() => {
    let dead = false;
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
  }, [docPath, docSlug, sel.chatId, docNonce, mock]);

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
      if (!r) return;
      openPage({ path: r.path, slug: r.slug, label: (r.path.split("/").pop() ?? r.path).replace(/\.md$/, "") });
    };
    const onMeeting = (e: Event) => {
      const ref = (e as CustomEvent<{ ref?: string }>).detail?.ref;
      if (!ref) return;
      const native = ref.includes("/") ? ref.slice(ref.indexOf("/") + 1) : ref;
      const m = meetings.find((x) => (x as { native_id?: string }).native_id === native || String(x.id) === ref);
      if (m) void openMeeting(m);
    };
    window.addEventListener(OPEN_ENTITY_EVENT, onEntity);
    window.addEventListener(OPEN_MEETING_EVENT, onMeeting);
    return () => { window.removeEventListener(OPEN_ENTITY_EVENT, onEntity); window.removeEventListener(OPEN_MEETING_EVENT, onMeeting); };
  }, [meetings, openMeeting, openPage]);

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
  const flavor = sel.kind === "meeting" ? `meeting · ${selMeeting ? PHASE_WORD[meetingPhase(selMeeting)] : "held"}`
    : sel.workspaces.filter((w) => w !== "_global").length === 0 ? "chat · admin" : "chat";

  // `?ask=<preset>` — the emailed link. App.tsx stashed the name; resolve it to an ADMIN-AUTHORED
  // body in `_global/asks/<name>.md` and open a fresh chat already holding it. The preset also says
  // which workspaces the chat is over, so context and opening prompt arrive together — which is the
  // whole point of the link. Editing the file changes every future click; nothing is rebuilt.
  // The chat is created TOUCHED: today nothing distinguishes a link the user clicked from one a
  // flow injected, and the safe reading of an ambiguous case is "the human meant to be here".
  const presetFired = useRef(false);
  useEffect(() => {
    if (presetFired.current) return;
    let raw: string | null = null;
    try { raw = localStorage.getItem("vexa.pendingPreset"); } catch { /* ignore */ }
    if (!raw) return;
    presetFired.current = true;
    try { localStorage.removeItem("vexa.pendingPreset"); } catch { /* ignore */ }
    let intent: { ask?: string; ws?: string; meeting?: string };
    try { intent = JSON.parse(raw) as typeof intent; } catch { return; }
    const name = (intent.ask || "").trim();
    // a NAME, and only a name — no slashes, no dots, nothing that walks out of asks/
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/i.test(name)) return;
    void (async () => {
      const body = await readWorkspaceFile(`asks/${name}.md`, { slug: "_global" }).catch(() => null);
      // an unknown preset opens nothing. Never fall back to text from the URL.
      if (!body || !body.trim()) return;
      // optional frontmatter: `mounts:` (comma-separated) and `label:`
      let text = body, mounts: string[] = [], label = name.replace(/[-_]/g, " ");
      const fm = /^---\n([\s\S]*?)\n---\n?/.exec(body);
      if (fm) {
        text = body.slice(fm[0].length);
        const m = /^mounts:\s*(.+)$/m.exec(fm[1]);
        if (m) mounts = m[1].split(",").map((x) => x.trim()).filter(Boolean);
        const l = /^label:\s*(.+)$/m.exec(fm[1]);
        if (l) label = l[1].trim();
      }
      if (intent.ws) mounts = [intent.ws, ...mounts.filter((x) => x !== intent.ws)];
      if (!mounts.length) mounts = ["_global", "personal"];
      const prompt = text
        .replace(/\{\{\s*meeting\s*\}\}/g, intent.meeting || "the meeting in view")
        .replace(/\{\{\s*ws\s*\}\}/g, mounts[0] || "")
        .replace(/\{\{\s*today\s*\}\}/g, new Date().toISOString().slice(0, 10))
        .trim();
      if (!prompt) return;
      // NOT dispatching OPEN_MEETING_EVENT: its handler would replace the selection made here and
      // take the preset's mounts with it. The ref reaches the agent through the {{meeting}}
      // substitution, and it can open the meeting itself.
      // same settle delay the other seeded conversations use — the chat must be mounted to hear it
      addChat(label, mounts, { id: `askchat-${Date.now().toString(36)}`, kick: prompt });
    })();
  }, [addChat]);

  return (
    <div style={{ position: "relative", display: "grid", gridTemplateColumns: `${T.railW}px minmax(0, 1fr) ${pagesW}px`, gridTemplateRows: `${T.headerH}px 1fr`, height: "100%", minHeight: 0, background: surface.rail }}>
      <Rail rows={shownRows} hidden={hiddenCount} all={all} onAll={toggleAll}
        selKey={selKey} onSelect={(r) => void openRow(r)}
        onNewChat={() => addChat("New chat", ["personal", "_global"])} onDeleteChat={deleteChat} />
      <ContextBar sel={sel} flavor={flavor} memberships={memberships}
        onAddWorkspace={(id) => setWorkspaces((ws) => ws.includes(id) ? ws : [...ws, id])}
        onRemoveWorkspace={(id) => setWorkspaces((ws) => ws.filter((w) => w !== id))} />
      <main style={{ gridRow: 2, gridColumn: 2, minWidth: 0, minHeight: 0, background: surface.center }}>
        <Chat params={{ session }} emptyExtra={<ProposalChips items={chips} onPick={(p) => void runProposal(p)} />} />
      </main>
      {/* the pages panel's resize handle — a real separator: 11px hit area, a hairline that
          lights up on hover/focus, and arrow keys for anyone not dragging */}
      <div role="separator" aria-orientation="vertical" aria-label="Resize pages panel" tabIndex={0}
        onMouseDown={startDrag}
        onKeyDown={(e) => { if (e.key === "ArrowLeft") { e.preventDefault(); nudge(24); } if (e.key === "ArrowRight") { e.preventDefault(); nudge(-24); } }}
        onMouseEnter={(e) => { (e.currentTarget.firstElementChild as HTMLElement).style.background = "var(--accent)"; }}
        onMouseLeave={(e) => { if (!dragging.current) (e.currentTarget.firstElementChild as HTMLElement).style.background = "transparent"; }}
        onFocus={(e) => { (e.currentTarget.firstElementChild as HTMLElement).style.background = "var(--accent)"; }}
        onBlur={(e) => { (e.currentTarget.firstElementChild as HTMLElement).style.background = "transparent"; }}
        style={{ position: "absolute", top: 0, bottom: 0, right: pagesW - 5, width: 11, cursor: "col-resize", zIndex: 5, display: "flex", justifyContent: "center", outline: "none" }}>
        <span style={{ width: 1, alignSelf: "stretch", background: "transparent", transition: "background .12s" }} />
      </div>
      <PagesPanel pages={pages} docPath={docPath} docSlug={docSlug}
        onOpen={openPage} onClose={closeTab}
        listing={listing} onNavigate={(slug, prefix) => void navigate(slug, prefix)}
        canBack={canBack} canForward={canForward} onBack={goBack} onForward={goForward}
        body={docBody} onSaved={() => setDocNonce((n) => n + 1)} />
    </div>
  );
}

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
import { ASK_CHAT_EVENT, CHAT_TOUCHED_EVENT, OPEN_ENTITY_EVENT, OPEN_MEETING_EVENT } from "../canvas/actions";
import { Chat } from "../surfaces/chat";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import { readActiveSet, setSharedActive, deactivateWorkspace, readWorkspaceFile } from "../surfaces/workspaceApi";
import { ContextBar } from "./ContextBar";
import { PagesPanel } from "./PagesPanel";
import {
  chatForRow, loadChats, loadRailAll, markTouched, meetingTitle, newChat, railRows, removeChat,
  saveChats, saveRailAll, upsertChat, visibleRows, PERSONAL_CHAT_ID,
  type Chat as ChatRec, type Row,
} from "./chats";
import { resolveDocRef } from "../ui-kit/docLinks";
import { Rail } from "./Rail";
import { meetingPhase, type MeetingMock } from "../surfaces/meetingModel";
import { pagesForPhase, resolveView, VIEW_KEY } from "./roomView";
import { MOCK_CHATS, MOCK_MEETINGS, mockBody, mockOn } from "./mockPhases";
import { T, surface } from "./tokens";
import type { Page, Sel } from "./types";

const PERSONAL_SEL: Sel = { kind: "chat", chatId: PERSONAL_CHAT_ID, label: "Personal", workspaces: ["personal", "_global"] };

export function MinutesShell() {
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
  const [docPath, setDocPath] = useState("README.md");
  const [docSlug, setDocSlug] = useState<string | undefined>(undefined);
  const [docBody, setDocBody] = useState<string | null>(null);
  const [docNonce, setDocNonce] = useState(0);

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
    return Number.isFinite(n) && n >= T.pagesMin && n <= T.pagesMax ? n : T.pagesDefault;
  });
  const dragging = useRef(false);
  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!dragging.current) return;
      e.preventDefault();
      const room = window.innerWidth - T.railW - 420;   // never squeeze the conversation below ~420
      const w = Math.min(Math.min(T.pagesMax, room), Math.max(T.pagesMin, window.innerWidth - e.clientX));
      setPagesW(w);
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
  const startDrag = () => { dragging.current = true; document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none"; };
  const nudge = (d: number) => setPagesW((w) => {
    const n = Math.min(T.pagesMax, Math.max(T.pagesMin, w + d));
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

  /** Open a chat. The ONE place a room's artifacts land: the chat's `meeting` ref decides whether
   *  this is the meeting layout (prep vs has-transcript, transcript on the right) or a workspace
   *  room, and a pending `?view=` then seeds which artifact opens, exactly once. */
  const openChat = useCallback(async (c: ChatRec) => {
    const m = c.meeting ? meetings.find((x) => String(x.id) === c.meeting) : undefined;
    setSel({
      kind: c.meeting ? "meeting" : "chat",
      chatId: c.id,
      meetingId: c.meeting,
      label: c.label || (m ? meetingTitle(m) : "Chat"),
      workspaces: c.workspaces,
    });
    setDocBody(null); setDocNonce((n) => n + 1);
    const openPages = (p: Page[]) => {
      let list = p, focus: Page | null = null;
      if (!viewSpent.current && pendingView) {
        viewSpent.current = true;
        try { localStorage.removeItem(VIEW_KEY); } catch { /* locked-down storage */ }
        ({ pages: list, focus } = resolveView(pendingView, p));
      }
      const front = focus ?? list[0];
      setPages(list);
      if (front) { setDocPath(front.path); setDocSlug(front.slug); }
    };
    await mountSet(c.workspaces);
    if (c.meeting) {
      const native = (m as { native_id?: string } | undefined)?.native_id;
      // TWO layouts, keyed on whether a transcript exists yet (founder ruling): prep opens the
      // brief; live and post both lead with the transcript. `meetingPhase()` still returns three —
      // chat.tsx needs them for its mode chip — but live/post render the same room here.
      // It is a property of the MEETING, never of the link that opened it: an emailed link is
      // clicked at an unpredictable time, so a "prep" link followed after the meeting must not lie.
      openPages(pagesForPhase(m ? meetingPhase(m) : "post", native));
      return;
    }
    const shared = c.workspaces.filter((w) => w !== "_global");
    if (!shared.length) { openPages([{ path: "README.md", slug: "_global", label: "The organisation" }]); return; }
    const ps: Page[] = shared.map((w) => w === "personal"
      ? { path: "README.md", label: "personal" }
      : { path: "README.md", slug: w, label: w });
    ps.push({ path: "README.md", slug: "_global", label: "_global" });
    openPages(ps);
  }, [meetings, mountSet, pendingView]);

  /** Open a rail row. A row derived from a meeting has no chat yet — first open MATERIALISES one
   *  (id `meet-<meetingId>`, so it lands on the agent session that meeting has always used). */
  const openRow = useCallback(async (r: Row, opts: { touched?: boolean } = {}) => {
    const existing = r.chatId ? chatsRef.current.find((c) => c.id === r.chatId) : undefined;
    const c = existing ?? chatForRow(chatsRef.current, r, meetings);
    const want = opts.touched ? { ...c, touched: true } : c;
    if (!existing || opts.touched) persist((prev) => upsertChat(prev, want));
    await openChat(want);
  }, [meetings, openChat, persist]);

  const openMeeting = useCallback(async (m: MeetingMock, opts: { touched?: boolean } = {}) => {
    const id = String(m.id);
    const row = railRows(chatsRef.current, [m]).find((r) => r.meetingId === id);
    if (row) await openRow(row, opts);
  }, [openRow]);

  const addChat = useCallback((label: string, workspaces: string[], opts: { id?: string; kick?: string } = {}) => {
    const c = newChat(label, workspaces, { id: opts.id, touched: true });
    persist((prev) => upsertChat(prev, c));
    void openChat(c);
    if (opts.kick) setTimeout(() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { hidden: true, session: c.id, prompt: opts.kick } })), 1200);
    return c;
  }, [openChat, persist]);

  // Deleting a chat drops it from the rail (its agent session stays on the server — the row is the
  // user's index, not the record). A meeting's row comes back as a derived row, because the meeting
  // itself did not go anywhere.
  const deleteChat = (chatId: string) => {
    persist((prev) => removeChat(prev, chatId));
    if (sel.chatId === chatId) setSel(PERSONAL_SEL);
  };

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
      const label = (r.path.split("/").pop() ?? r.path).replace(/\.md$/, "");
      setPages((prev) => prev.some((pg) => pg.path === r.path && pg.slug === r.slug) ? prev : [...prev, { path: r.path, slug: r.slug, label }]);
      setDocPath(r.path); setDocSlug(r.slug); setDocNonce((n) => n + 1);
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
  }, [meetings, openMeeting]);

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
  const mounts = sel.kind === "meeting" ? "[_global · personal · _system] + meeting"
    : `[${[...new Set([...sel.workspaces, "_global"])].join(" · ")} · _system]`;

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
      <ContextBar sel={sel} flavor={flavor} mounts={mounts} />
      <main style={{ gridRow: 2, gridColumn: 2, minWidth: 0, minHeight: 0, background: surface.center }}>
        <Chat params={{ session }} />
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
      <PagesPanel pages={pages} docPath={docPath} docSlug={docSlug} onOpen={(pg) => { setDocPath(pg.path); setDocSlug(pg.slug); }} body={docBody} onSaved={() => setDocNonce((n) => n + 1)} />
    </div>
  );
}

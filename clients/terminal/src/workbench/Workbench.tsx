"use client";
/** term-workbench (v2) — the structured 3-pane shell.
 *  LEFT (resizable/collapsible): segmented list switcher + the active list.
 *  CENTER: dockview TABS — a "tab" host resolves each panel by params.kind via the tab registry.
 *  RIGHT (resizable/collapsible): the persistent workspace chat, grounded by the active center tab.
 *  Reuses the Phase-C ⌘K palette + keybindings; the kernel's services do the rest. */
import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties } from "react";
import { Allotment } from "allotment";
import "allotment/dist/style.css";
import { DockviewReact, type DockviewApi, type DockviewReadyEvent, type IDockviewPanelProps, type IDockviewPanelHeaderProps, themeAbyss } from "dockview-react";
import "dockview/dist/styles/dockview.css";

const PANES_KEY = "vexa.terminal.panes.v2";
const savedSizes = (): number[] | undefined => { try { const s = localStorage.getItem(PANES_KEY); const a = s ? JSON.parse(s) : null; return Array.isArray(a) && a.length === 3 ? a : undefined; } catch { return undefined; } };
const persistSizes = (s: number[]) => { try { localStorage.setItem(PANES_KEY, JSON.stringify(s)); } catch { /* noop */ } };
import { useService, useStore, KeybindingServiceId } from "../platform";
import { LayoutServiceId } from "./layout";
import { CommandPalette } from "./CommandPalette";
import { registry } from "../contributions";
import { Icon } from "../ui-kit";
import { updatesBadge, markUpdatesSeen, updatesSeenTs } from "../surfaces/updatesBadge";
import { readActiveSet, readWorkspaceGit } from "../surfaces/workspaceApi";
import { ContextMenu, copyText } from "../ui-kit/ContextMenu";
import { Chat } from "../surfaces/chat";
import { resolveDocRef } from "../ui-kit/docLinks";
import { liveMeetingsNow } from "../surfaces/liveMeetings";
import { firstViewPlan } from "./firstView";
import { OPEN_ENTITY_EVENT } from "../canvas/actions";
import { useTheme } from "../app/theme";
import { meetingsOnly } from "../app/mode";

// ── theme toggle: dark ⇄ day mode, icon button in the profile row ──
function ThemeToggle() {
  const [theme, toggle] = useTheme();
  const day = theme === "light";
  return (
    <button onClick={toggle} title={day ? "Switch to dark mode" : "Switch to day mode"}
      style={{ flex: "none", display: "flex", alignItems: "center", padding: 4, borderRadius: 6, background: "none", border: "none", color: "var(--t3)", cursor: "pointer" }}>
      <Icon name={day ? "moon" : "sun"} size={15} />
    </button>
  );
}

// ── the dockview panel host: render a tab by its kind, tracking active state ─────
function TabHost(props: IDockviewPanelProps) {
  const layout = useService(LayoutServiceId);
  // dockview reuses ONE panel for the shared preview slot and swaps its params via updateParameters
  // WITHOUT re-rendering the React content — subscribe to the param-change event so single-clicking a
  // different meeting/file in the preview slot actually re-binds the content to the new params.
  const [params, setParams] = useState(props.params as { kind?: string; p?: Record<string, unknown> });
  useEffect(() => {
    const d = props.api.onDidParametersChange((next) => { if (next && Object.keys(next).length) setParams(next as { kind?: string; p?: Record<string, unknown> }); });
    return () => d.dispose();
  }, [props.api]);
  const kind = params.kind ?? "";
  const Comp = registry.tabComponent(kind);
  const [active, setActive] = useState<boolean>(props.api.isActive);
  useEffect(() => {
    const d = props.api.onDidActiveChange((e: { isActive: boolean }) => setActive(e.isActive));
    return () => d.dispose();
  }, [props.api]);
  useEffect(() => { if (active) layout.setActiveTab(kind ? { kind, params: params.p ?? {} } : null); }, [active, kind, layout, params.p]);
  if (!Comp) return <div style={{ padding: 24, color: "var(--t3)", fontSize: 13 }}>Unknown tab kind: {kind}</div>;
  return <Comp id={props.api.id} params={params.p ?? {}} active={active} />;
}
const dvComponents = { tab: TabHost };

// ── custom tab header: VS Code-style — PREVIEW tabs render their title in italic ──
function TabHeader(props: IDockviewPanelHeaderProps) {
  const layout = useService(LayoutServiceId);
  // params change in place (preview swaps, in-pane navigation, pin/unpin) — subscribe like TabHost does
  const [params, setParams] = useState(props.params as { kind?: string; p?: { path?: unknown; meetingId?: unknown }; preview?: boolean; pinned?: boolean });
  useEffect(() => {
    const d = props.api.onDidParametersChange((next) => { if (next && Object.keys(next).length) setParams(next as typeof params); });
    return () => d.dispose();
  }, [props.api]);
  const preview = Boolean(params.preview);
  const pinned = Boolean(params.pinned);
  const [title, setTitle] = useState<string>(props.api.title ?? "");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  useEffect(() => {
    const d = props.api.onDidTitleChange((e: { title: string }) => setTitle(e.title));
    return () => d.dispose();
  }, [props.api]);
  const path = typeof params.p?.path === "string" ? params.p.path : null;
  const meetingId = typeof params.p?.meetingId === "string" ? params.p.meetingId : null;
  const pinItem = pinned
    ? { id: "unpin-tab", label: "Unpin tab", onSelect: () => layout.unpinTab(props.api.id) }
    : { id: "pin-tab", label: "Pin tab", detail: "keep open until unpinned", onSelect: () => layout.pinTab(props.api.id) };
  const copyItems = params.kind === "doc" && path
    ? [
      { id: "copy-reference", label: "Copy reference", detail: `@file:${path}`, onSelect: () => copyText(`@file:${path}`) },
      { id: "copy-path", label: "Copy path", detail: path, onSelect: () => copyText(path) },
    ]
    : params.kind === "meeting" && meetingId
      ? [{ id: "copy-reference", label: "Copy reference", detail: `@meeting:${meetingId}`, onSelect: () => copyText(`@meeting:${meetingId}`) }]
      : [];
  return (
    <div
      className="dv-default-tab"
      onMouseDown={(e) => {
        if (e.button !== 1) return;
        e.preventDefault();
        e.stopPropagation();
        if (!pinned) props.api.close();
      }}
      onAuxClick={(e) => {
        if (e.button !== 1) return;
        e.preventDefault();
        e.stopPropagation();
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setMenu({ x: e.clientX, y: e.clientY });
      }}
      style={{ display: "flex", alignItems: "center", height: "100%" }}
    >
      {/* left-click pin toggle: always visible so pinning is one click (pinned = solid accent) */}
      <span role="button" aria-label={pinned ? "Unpin tab" : "Pin tab"}
        title={pinned ? "Pinned — click to unpin" : "Pin tab (keeps it open until unpinned)"}
        onPointerDown={(e) => e.preventDefault()}
        onClick={(e) => { e.stopPropagation(); if (pinned) layout.unpinTab(props.api.id); else layout.pinTab(props.api.id); }}
        onMouseEnter={(e) => { e.currentTarget.style.opacity = "1"; }}
        onMouseLeave={(e) => { e.currentTarget.style.opacity = pinned ? "1" : "0.45"; }}
        style={{ display: "flex", alignItems: "center", marginRight: 5, cursor: "pointer",
          color: pinned ? "var(--blue)" : "var(--t3)", opacity: pinned ? 1 : 0.45 }}>
        <Icon name="pin" size={11} />
      </span>
      <span className="dv-default-tab-content" style={{ fontStyle: preview ? "italic" : "normal" }}>{title}</span>
      {/* Chrome semantics: a pinned tab has no close affordance until unpinned */}
      {!pinned && (
        <span
          className="dv-default-tab-action"
          role="button"
          aria-label="Close tab"
          onPointerDown={(e) => e.preventDefault()}
          onClick={(e) => { e.stopPropagation(); props.api.close(); }}
        >×</span>
      )}
      {menu && <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} items={[pinItem, ...copyItems]} />}
    </div>
  );
}
const dvTabComponents = { default: TabHeader };

// ── LEFT pane: brand + segmented list switcher + active list ─────────────────────
function LeftPane() {
  const layout = useService(LayoutServiceId);
  const { activeList } = useStore(layout.store);
  // re-render on LATE registrations (e.g. the admin surface appears after its async gate check)
  useSyncExternalStore(registry.subscribe, registry.version, registry.version);
  const lists = registry.lists();
  const active = registry.list(activeList) ?? lists[0];
  const Comp = active?.component;
  // "new updates" badge on the Knowledge nav — OTHER members' commits across the caller's active
  // workspaces since Knowledge was last opened. Polled here (always mounted) so it updates even when the
  // user is on Meetings/Sessions; opening Knowledge clears it.
  const badge = useSyncExternalStore(updatesBadge.subscribe, updatesBadge.count, () => 0);
  const newestRef = useRef(0);
  useEffect(() => {
    const poll = async () => {
      if (document.hidden) return;
      try {
        const mounts = (await readActiveSet()).active;
        const gits = await Promise.all(mounts.map((m) => readWorkspaceGit(m.primary ? undefined : { slug: m.slug }).catch(() => null)));
        const member = gits.flatMap((g) => (g ? g.commits : [])).filter((c) => c.kind === "member" && (c.ts ?? 0) > 0);
        newestRef.current = member.reduce((mx, c) => Math.max(mx, c.ts ?? 0), 0);
        updatesBadge.set(member.filter((c) => (c.ts ?? 0) > updatesSeenTs()).length);
      } catch { /* additive — a poll failure just leaves the badge as-is */ }
    };
    void poll();
    const iv = setInterval(() => void poll(), 6000);
    const onFocus = () => void poll();
    window.addEventListener("focus", onFocus);
    return () => { clearInterval(iv); window.removeEventListener("focus", onFocus); };
  }, []);
  useEffect(() => { if (activeList === "files") markUpdatesSeen(newestRef.current || Math.floor(Date.now() / 1000)); }, [activeList]);
  const seg = (on: boolean): CSSProperties => ({ display: "flex", alignItems: "center", gap: 6, padding: "5px 9px", borderRadius: 7, fontSize: 12.5, cursor: "pointer", border: "none", color: on ? "var(--t1)" : "var(--t2)", background: on ? "var(--panel2)" : "transparent", flex: "none", whiteSpace: "nowrap" });
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", background: "var(--sidebar)", borderRight: "1px solid var(--line)", minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "12px 14px 8px", flex: "none" }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/vexa-logo.svg" alt="Vexa" width={24} height={24} style={{ borderRadius: 7, display: "block", flex: "none" }} />
        <span style={{ fontSize: 13, fontWeight: 500, color: "var(--t1)" }}>Vexa <span style={{ fontWeight: 400, color: "var(--t3)" }}>terminal</span></span>
      </div>
      {/* stacked vertically — every list is visible at any sidebar width (no horizontal
          overflow/scroll), matching the file-tree rows below */}
      <div style={{ display: "flex", flexDirection: "column", gap: 2, padding: "2px 8px 8px", borderBottom: "1px solid var(--line)", flex: "none" }}>
        {lists.map((l) => (
          <button key={l.id} style={seg(l.id === active?.id)} onClick={() => layout.setActiveList(l.id)} title={l.label}>
            <Icon name={l.icon} size={13} />{l.label}
            {l.id === "files" && badge > 0 && (
              <span title={`${badge} new update${badge > 1 ? "s" : ""} from other members`}
                style={{ marginLeft: "auto", background: "var(--accent)", color: "var(--bg)", fontSize: 10, fontWeight: 700, borderRadius: 9, minWidth: 16, textAlign: "center", padding: "0 5px", lineHeight: "16px", flex: "none" }}>
                {badge}
              </span>
            )}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>{Comp && <Comp />}</div>
      <UserProfile />
    </div>
  );
}

// ── LEFT pane footer: the signed-in user's profile (replaces the old "Self-hosted · air-gapped" label).
//    Identity comes from /api/auth/me (the vexa-user-info cookie). The avatar shows initials; the sign-out
//    control stays here so logout is always reachable. Wiping client state on logout keeps the next user
//    from inheriting this one's tabs/docs/focus.
function UserProfile() {
  const [user, setUser] = useState<{ email?: string | null; name?: string | null } | null>(null);
  useEffect(() => {
    let active = true;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => active && setUser((d?.user as { email?: string; name?: string } | undefined) ?? null))
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  const email = user?.email ?? "";
  const name = (user?.name || (email ? email.split("@")[0] : "") || "Account").trim();
  const initials = (name.match(/\b[a-z0-9]/gi) || []).slice(0, 2).join("").toUpperCase() || "?";

  const signOut = () => {
    void fetch("/api/auth/logout", { method: "POST" }).finally(() => {
      try { localStorage.clear(); sessionStorage.clear(); } catch { /* storage unavailable */ }
      window.location.reload();
    });
  };

  return (
    <div style={{ padding: "8px 12px", borderTop: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 9, flex: "none" }}>
      <div style={{ width: 26, height: 26, borderRadius: "50%", background: "var(--panel2)", color: "var(--t1)", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 600, fontSize: 11, flex: "none" }}>{initials}</div>
      <div style={{ minWidth: 0, flex: 1, lineHeight: 1.25 }}>
        <div style={{ fontSize: 12.5, fontWeight: 500, color: "var(--t1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</div>
        {email && <div style={{ fontSize: 11, color: "var(--t3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{email}</div>}
      </div>
      <ThemeToggle />
      <button type="button" title="Sign out" onClick={signOut}
        style={{ flex: "none", background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", padding: 4, borderRadius: 6 }}>
        <Icon name="logout" size={15} />
      </button>
    </div>
  );
}

// ── RIGHT pane: persistent chat singleton ────────────────────────────────────────
function RightPane() {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", background: "var(--rail)", borderLeft: "1px solid var(--line)", minHeight: 0 }}>
      <div style={{ flex: 1, minHeight: 0 }}>
        <Chat />
      </div>
    </div>
  );
}

// ── the shell ───────────────────────────────────────────────────────────────────
export function Workbench() {
  const layout = useService(LayoutServiceId);
  const keybindings = useService(KeybindingServiceId);
  const { leftCollapsed, rightCollapsed, activeList, activeTab } = useStore(layout.store);
  // ── responsive tiers — derived from window width, never mutating the user's saved
  //    collapse state (widening the window restores exactly what was there).
  //    full ≥900: three panes · narrow <900: left sidebar hides · single <560 (≈¼ screen):
  //    ONE pane — the center when something is open there (meeting/doc), otherwise chat.
  const [winW, setWinW] = useState(() => (typeof window === "undefined" ? 1440 : window.innerWidth));
  useEffect(() => {
    const on = () => setWinW(window.innerWidth);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  const tier = winW < 560 ? "single" : winW < 900 ? "narrow" : "full";
  // CHAT-ONLY mode: the Sessions view is left-sidebar + chat, no center canvas. New users land here
  // (default list = "sessions") so onboarding is just the conversation; Meetings/Files/Routines reveal
  // the full 3-pane interface.
  // Meetings-only mode: no agent chat rail at all — 2-pane shell (a stale persisted "sessions"
  // activeList must not flip it into chat-only either; that list doesn't register in this mode).
  const meetOnly = meetingsOnly();
  const chatOnly = !meetOnly && activeList === "sessions";
  useEffect(() => { const d = keybindings.attach(window); return () => d.dispose(); }, [keybindings]);

  // A pending SHARED landing — an accepted invite (`vexa.openWorkspace`) or a shared meeting
  // (`vexa.openMeeting`), stashed by InviteGate before the reload — must place a tab in the DOCKVIEW. But
  // the default Sessions view is CHAT-ONLY: the dockview isn't mounted, so its onReady (→ resolveFirstView)
  // would never fire and the share would silently land on the session chat instead. Flip OFF chat-only
  // EARLY here (peek only — resolveFirstView consumes the stash) so the grid mounts and the resolver runs.
  useEffect(() => {
    if (meetOnly || layout.store.getState().activeList !== "sessions") return;
    try {
      if (localStorage.getItem("vexa.openWorkspace")) layout.setActiveList("files");
      else if (localStorage.getItem("vexa.openMeeting")) layout.setActiveList("meetings");
    } catch { /* noop — a locked-down localStorage just means no early reveal */ }
    // once, on mount (a fresh page load after the redeem reload) — deps intentionally empty
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Clicking an entity link in chat or a doc opens its doc tab. Reveal the center (leave chat-only →
  // Knowledge view), resolve [[wikilinks]]/relative paths via the shared resolver (slug-aware: the
  // source doc's workspace first, then home, then the rest of the mounted set).
  useEffect(() => {
    const onOpenEntity = async (e: Event) => {
      const detail = (e as CustomEvent<{ path?: string; wikilink?: string; slug?: string; docPath?: string; beside?: boolean }>).detail || {};
      const r = await resolveDocRef(detail, { path: detail.docPath, slug: detail.slug });
      if (!r) return;
      if (layout.store.getState().activeList === "sessions") layout.setActiveList("files");  // reveal the center
      const d = { id: r.slug ? `doc:${r.slug}:${r.path}` : `doc:${r.path}`, title: r.path.split("/").pop() ?? r.path, kind: "doc", params: { path: r.path, slug: r.slug } };
      // beside = clicked inside a doc → split, keep the source visible; otherwise plain tab.
      if (detail.beside) layout.openTabBeside(d); else layout.openTab(d);
    };
    window.addEventListener(OPEN_ENTITY_EVENT, onOpenEntity);
    return () => window.removeEventListener(OPEN_ENTITY_EVENT, onOpenEntity);
  }, [layout]);

  // detach the dockview api on unmount (navigation/HMR dispose it) so the layout
  // service never operates on a disposed grid.
  const apiRef = useRef<DockviewApi | null>(null);
  useEffect(() => () => { if (apiRef.current) layout.detach(apiRef.current); }, [layout]);

  // ── FIRST-VIEW RESOLVER — on landing, pick ONE arrangement by what's SHARED with the user, replacing
  // the scattered self-firing auto-opens (the old tshare effect + the empty-dock live-open + the shared-
  // README pin that only fired once Knowledge was opened). Priority (the product spec):
  //   shared meeting + shared workspace → pin the workspace README, open the meeting (its live badge shows)
  //   shared meeting only               → open the meeting
  //   shared workspace only             → pin the shared workspace README
  //   nothing shared (fresh dock)       → the user's own README-onboarding (or a known live meeting)
  // `fresh` = the dock restored no tabs (a genuine first landing). A returning user with a saved layout
  // gets ONLY the explicit shared-meeting arm (they clicked a share link) — never a surprise re-pin.
  const firstViewDone = useRef(false);
  const resolveFirstView = async (fresh: boolean) => {
    // an explicit shared meeting from a ?tshare= link (InviteRedeemer stashed it before the reload)
    let sharedMeetingId: string | null = null;
    try { sharedMeetingId = localStorage.getItem("vexa.openMeeting"); if (sharedMeetingId) localStorage.removeItem("vexa.openMeeting"); } catch { /* noop */ }
    // a workspace whose invite the user just accepted (InviteRedeemer stashed its id before the reload) —
    // pin its README regardless of a saved dock, so an accepted share always lands on the shared workspace.
    let acceptedSlug: string | null = null;
    try { acceptedSlug = localStorage.getItem("vexa.openWorkspace"); if (acceptedSlug) localStorage.removeItem("vexa.openWorkspace"); } catch { /* noop */ }
    // a shared workspace connected to this user (a non-primary 'shared' mount in the active set)
    let sharedSlug: string | null = null;
    // the HOME mount (first active) — "own README" means ITS README (ADR-0028: reads are
    // slug-addressed; the seed-slot dir is a storage detail, not "the user's workspace")
    let homeSlug: string | undefined;
    try {
      const set = await readActiveSet();
      sharedSlug = set.active.find((m) => !m.primary && m.role === "shared")?.slug ?? null;
      homeSlug = (set.active.find((m) => m.primary) ?? set.active[0])?.slug;
    } catch { /* active-set read failed — treat as no shared workspace */ }
    if (!apiRef.current) return;  // grid torn down while we awaited — nothing to arrange

    const revealCenter = () => { if (!meetOnly && layout.store.getState().activeList === "sessions") layout.setActiveList("files"); };
    const openMeeting = (mid: string, reveal: boolean) => {
      if (reveal && !meetOnly) layout.setActiveList("meetings");
      layout.openTab({ id: `meeting:${mid}`, title: "Shared meeting", kind: "meeting", params: { meetingId: mid } });
    };
    // `forceKnowledge` = land ON the Knowledge section unconditionally (an accepted invite is explicit —
    // the shared workspace's tree must show, even for a returning user whose saved rail was Meetings/etc).
    // Otherwise just reveal the center out of chat-only (sessions → files).
    const pinReadme = (slug?: string, forceKnowledge = false) => {
      if (forceKnowledge && !meetOnly) layout.setActiveList("files"); else revealCenter();
      // coordinate with MountSection's once-per-session pin (workspace.tsx) so it doesn't double-pin later
      if (slug) { try { sessionStorage.setItem(`vexa.readme.pinned.${slug}`, "1"); } catch { /* noop */ } }
      layout.openTab({ id: slug ? `doc:${slug}:README.md` : "doc:README.md", title: "README.md", kind: "doc", params: { path: "README.md", slug } });
    };

    const plan = firstViewPlan({ sharedMeetingId, acceptedSlug, sharedSlug, liveMeetingId: liveMeetingsNow()[0]?.id ?? null, fresh });
    switch (plan.kind) {
      case "meeting-and-workspace": openMeeting(plan.meetingId, false); pinReadme(plan.slug, !!acceptedSlug); break;  // README pinned last → focused
      case "meeting":               openMeeting(plan.meetingId, true); break;
      case "workspace-readme":      pinReadme(plan.slug, !!acceptedSlug); break;  // accepted invite → force Knowledge open
      case "live-meeting":          openMeeting(plan.meetingId, true); break;
      case "own-readme":            pinReadme(homeSlug); break;
      case "noop":                  break;
    }
  };

  const onReady = (e: DockviewReadyEvent) => {
    apiRef.current = e.api;
    layout.attach(e.api);
    if (firstViewDone.current) return;             // once per app load (guards remounts / HMR)
    firstViewDone.current = true;
    void resolveFirstView(e.api.panels.length === 0);
  };

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: "var(--bg)", color: "var(--t1)" }}>
      <div style={{ height: 38, display: "flex", alignItems: "center", gap: 12, padding: "0 12px", borderBottom: "1px solid var(--line)", background: "var(--sidebar)", flex: "none" }}>
        <button aria-label="Toggle left" onClick={() => layout.toggleLeft()} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex" }}><Icon name="panel" size={16} /></button>
        <div style={{ flex: 1 }} />
        <button aria-label="Toggle right" onClick={() => layout.toggleRight()} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", transform: "scaleX(-1)" }}><Icon name="panel" size={16} /></button>
      </div>

      <div style={{ flex: 1, minHeight: 0 }}>
        {/* chat-only (Sessions) gets its own sizes — the freed center space goes to the CHAT (right), with a
            narrow left sidebar; full mode keeps the user's saved 3-pane sizes. The `key` re-lays-out on switch. */}
        {(() => {
          // single-pane resolution: center wins when it has content, else chat.
          const centerHasContent = !chatOnly && activeTab != null;
          const showLeft = !leftCollapsed && tier === "full";
          const showCenter = !chatOnly && (tier !== "single" || centerHasContent || meetOnly);
          const showRight = !meetOnly && (tier === "single"
            ? !(showCenter)                                   // ¼-width: exactly one pane
            : (chatOnly || !rightCollapsed));
          return (
            <Allotment
              key={`${chatOnly ? "chat-only" : "full"}-${tier}`}
              onChange={(s) => { if (!chatOnly && tier === "full") persistSizes(s); }}
              defaultSizes={chatOnly ? [20, 80] : (savedSizes() ?? [15, 55, 30])}
            >
              <Allotment.Pane visible={showLeft} minSize={180} preferredSize={chatOnly ? "20%" : "15%"}>
                <LeftPane />
              </Allotment.Pane>
              {!chatOnly && (
                <Allotment.Pane visible={showCenter} minSize={tier === "full" ? 360 : 200} preferredSize="55%">
                  <div style={{ height: "100%", position: "relative" }}>
                    <div style={{ position: "absolute", inset: 0 }}>
                      <DockviewReact onReady={onReady} components={dvComponents} tabComponents={dvTabComponents} defaultTabComponent={TabHeader} theme={themeAbyss} />
                    </div>
                  </div>
                </Allotment.Pane>
              )}
              {/* meetings-only: the chat rail never MOUNTS (not merely hidden) — no agent fetches fire */}
              {!meetOnly && (
                <Allotment.Pane visible={showRight} minSize={tier === "full" ? 300 : 200} preferredSize={chatOnly ? "80%" : "30%"}>
                  <RightPane />
                </Allotment.Pane>
              )}
            </Allotment>
          );
        })()}
      </div>

      <footer style={{ height: 24, flex: "none", background: "var(--sidebar)", borderTop: "1px solid var(--line)", display: "flex", alignItems: "center", fontSize: 11.5, color: "var(--t2)" }}>
        <div style={{ flex: 1 }} />
        <button onClick={() => layout.resetLayout()} style={{ padding: "0 10px", height: "100%", background: "none", border: "none", color: "var(--t3)", cursor: "pointer" }} title="Reset layout">reset layout</button>
      </footer>

      <CommandPalette />
    </div>
  );
}

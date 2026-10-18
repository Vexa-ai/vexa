"use client";
/** THE WORKSPACE PAGE (#1783, R3 `home2`; approved mock proto-on-product 382a319b5, founder
 *  2026-10-16: "a full proper sidebar page for the workspace and that's not the md README… render a
 *  page from ready components, but richer").
 *
 *  A status card's click opens it in the RIGHT panel (UI decision 7c). Top to bottom, each section
 *  collapsible and ABSENT WHEN EMPTY: header (mark, name, kind, people, acts) · Now · Needs you ·
 *  Rooms · Your chats here · Agents and routines · Calls · Cards · Files and pages · People ·
 *  Activity · Metrics. "Expand" widens the panel and the page lays out in two columns.
 *
 *  Every section reads an owner the product already has, with the reader's own key: the attention
 *  read model for one workspace (E1: needs, rooms, chats, routines, calls — rule-based, access
 *  filtered), the desktop's tile (people, live call), the desk's cards (crm-api), the workspace tree
 *  and its git history. A workspace the reader can only read shows no acts and no editing. The README
 *  is an ordinary document, one click away. */
import { workspaceDisplayName } from "../../vocabulary";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent, type ReactNode } from "react";
import { Bot, CalendarDays, ChevronDown, ChevronRight, FileText, Hash, Lock, Maximize2, MessageSquare, Minimize2, MoreHorizontal, Play, Video } from "lucide-react";
import type { CardItem, WorkspaceItem } from "../../../platform/desktop";
import { CALL_START_EVENT, NEW_CHAT_AT_EVENT, NEW_ROOM_EVENT, OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../../platform/events";
import { listWorkspaceTree, readWorkspaceFile, readWorkspaceHistory, type GitCommit } from "../../../data/workspaceApi";
import { Button, ListRow, Menu, RowCell } from "../../../ui-kit";
import { useFeature } from "../../../platform/features";
import { NeedRow as RowNeed } from "../../shell2/NeedRow";
import { PeopleLine, WorkspacePeopleSection, lastChange } from "../../shell2/WorkspacePeople";
import { nameOrYou } from "../../shell2/me";
import { useConfirm } from "../../../ui-kit/primitives/Dialog";
import { useDesktopState } from "../desktopApi";
import { openCard, updateCard } from "../cardsApi";
import { askAgent, openDocOnRight, planNeedAct, resolveProposal } from "./acts";
import { RichPage } from "./RichPage";
import { readWorkspaceAttention, useAttention, type Need, type WorkspaceAttention } from "./attentionApi";
import { aimHome, wsHue, WsMark } from "./HomeTarget";
import { Stats } from "./RichPage";
import { splitFrontMatter } from "./richParse";
import { setSettings, useWorkspaceSettings } from "./settingsApi";
import { savePolicy, usePolicy } from "../../notificationPolicy";
import { humanActivity, peoplePages } from "./workspaceNoise";
import { InviteDialog } from "./NewWorkspace";
import { useFeature as useReadmeFeature } from "../../../platform/features";
import { ReadmeView } from "../../readme/ReadmeView";
import { copyReference } from "../../../surfaces/reference";
import { INVITE_EVENT } from "./acts";
import "./home2.css";

import { WORKSPACE_PAGE_WIDE_EVENT } from "./workspaceView";
export { WORKSPACE_PAGE_PATH, WORKSPACE_PAGE_WIDE_EVENT, isWorkspacePagePath } from "./workspaceView";

const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));
const PERSONAL = "personal";
const REFRESH_MS = 30_000;
const FILES_SHOWN = 8;
const ACTIVITY_SHOWN = 20;

export function ago(at: number | null | undefined, now: number = Date.now() / 1000): string {
  if (!at) return "";
  const s = Math.max(0, now - at);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400) return `${Math.round(s / 3600)} h`;
  if (s < 2 * 86400) return "yesterday";
  return `${Math.round(s / 86400)} d`;
}
const pageName = (p: string) => (/^readme\.md$/i.test(p) ? "README" : p.split("/").pop()!.replace(/\.(md|json)$/, "").replace(/^\d{4}-\d{2}-\d{2}-/, "").replace(/[-_]/g, " ").replace(/^\w/, (c) => c.toUpperCase()));

/** A section of the page. Whether it is folded is the person's own setting about this workspace
 *  (`collapsed` on the CRM member_of relation), never browser storage. */
function Section({ id, title, count, children, extra }: { id: string; title: string; count?: number; children: ReactNode; extra?: ReactNode }) {
  const fold = useContext(FoldContext);
  const open = !fold.collapsed.includes(id);
  return (
    <section className="vx-wsp-sec" data-sec={id} data-open={open ? "" : undefined}>
      <header><button type="button" className="vx-wsp-sec-h" aria-expanded={open} onClick={() => fold.toggle(id)}>
        {open ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}<span>{title}</span>{typeof count === "number" && <i>{count}</i>}</button>
        {extra && <span className="vx-wsp-sec-x">{extra}</span>}</header>
      {open && <div className="vx-wsp-sec-b">{children}</div>}
    </section>
  );
}
const FoldContext = createContext<{ collapsed: string[]; toggle: (id: string) => void }>({ collapsed: [], toggle: () => undefined });

/** The runs as a strip, oldest on the left (the history arrives newest first). */
function Spark({ runs }: { runs: { ok: boolean }[] }) {
  return <span className="vx-wsp-runs" aria-label={`${runs.filter((r) => r.ok).length} of ${runs.length} runs succeeded`}>
    {[...runs].reverse().map((r, i) => <i key={i} data-ok={r.ok ? "" : undefined} />)}</span>;
}

/** A routine's recent runs, newest first: when, ✓ or ⚠, and what happened — folded by default. */
function RunHistory({ runs }: { runs: { at: number; ok: boolean; what: string }[] }) {
  const [open, setOpen] = useState(false);
  return <div className="vx-wsp-rt-hist" data-run-history="">
    <button type="button" className="vx-pb-act" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
      {open ? "Hide runs" : `Last ${runs.length} runs`}</button>
    {open && <ol className="vx-rp-runs">{runs.map((x, i) => <li key={i} data-ok={x.ok ? "" : undefined}>
      <b>{x.ok ? "✓" : "⚠"} {x.at ? ago(x.at) : ""}</b> {humanStatus(x.what)}</li>)}</ol>}
  </div>;
}

export { humanStatus } from "./humanStatus";
import { humanStatus } from "./humanStatus";

const NEED_WORD: Record<Need["kind"], string> = { failed: "⚠ Failed", mention: "Mention", card: "Card", upcoming: "Upcoming", proposal: "Proposal" };

function NeedRow({ n, readOnly, run }: { n: Need; readOnly: boolean; run: (n: Need) => void }) {
  const diff = n.act.diff as { file?: string; before?: string[]; after?: string[] } | undefined;
  return (
    <li className="vx-wsp-need" data-kind={n.kind} data-need={n.id}>
      <div className="vx-wsp-need-top"><span className="vx-wsp-need-k">{NEED_WORD[n.kind]}</span><span className="vx-wsp-need-t">{humanStatus(n.text)}</span></div>
      {diff?.before && diff.after && <div className="vx-wsp-diff" aria-label={`Change to ${diff.file ?? "the page"}`}>{diff.file && <small>{diff.file}</small>}
        {diff.before.filter((x) => !diff.after!.includes(x)).map((x, i) => <div key={`b${i}`} data-d="-">− {x}</div>)}
        {diff.after.filter((x) => !diff.before!.includes(x)).map((x, i) => <div key={`a${i}`} data-d="+">+ {x}</div>)}</div>}
      {!readOnly && <div className="vx-wsp-need-acts">
        <button type="button" className="vx-kh-btn vx-kh-btn-primary" onClick={() => run(n)}>{n.act.label}</button>
        {n.act.kind === "accept-proposal" && <button type="button" className="vx-kh-btn" onClick={() => void resolveProposal(n.act, false)}>Decline</button>}
      </div>}
    </li>
  );
}

type FileRow = { path: string };
function useWorkspaceExtras(slug: string): { files: FileRow[]; commits: GitCommit[]; metrics: string | null; readme: string | null } {
  const [x, setX] = useState<{ files: FileRow[]; commits: GitCommit[]; metrics: string | null; readme: string | null }>({ files: [], commits: [], metrics: null, readme: null });
  useEffect(() => {
    let live = true;
    const opt = slug === PERSONAL ? undefined : { slug };
    void (async () => {
      const tree = await listWorkspaceTree(opt).catch(() => [] as string[]);
      // JTBD-1: the person's pages only; the agent's scaffolding (CLAUDE.md, flows, skills…) stays out
      const shownPages = peoplePages(tree);
      const readme = shownPages.find((f) => /^readme\.md$/i.test(f));
      const files = shownPages.map((path) => ({ path }));
      const history = await readWorkspaceHistory(slug, { limit: ACTIVITY_SHOWN }).catch(() => null);
      // METRICS only where the workspace names a stats source: `metrics: <file.json>` on its README
      let metrics: string | null = null;
      let readmeBody: string | null = null;
      if (readme) {
        const src = await readWorkspaceFile(readme, opt).catch(() => null);
        const fm = splitFrontMatter(src ?? "");
        readmeBody = src == null ? null : fm.body;
        const m = fm.meta.metrics?.trim();
        if (m && /\.json$/i.test(m) && !m.split("/").includes("..") && tree.includes(m)) metrics = m;
      }
      // …or the conventional root file a routine writes its figures to
      metrics ??= ["metrics.json", "kpis.json"].find((f) => tree.includes(f)) ?? null;
      if (live) setX({ files, commits: humanActivity(history?.commits ?? []), metrics, readme: readmeBody });
    })();
    return () => { live = false; };
  }, [slug]);
  return x;
}

export function WorkspacePage({ slug: given }: { slug?: string }) {
  const slug = given || PERSONAL;
  const { desktop } = useDesktopState(false);
  const w = (desktop?.sections.find((s) => s.id === "workspaces")?.items ?? [])
    .find((i) => i.kind === "workspace" && ((i as WorkspaceItem).slug || PERSONAL) === slug) as WorkspaceItem | undefined;
  const name = workspaceDisplayName(slug, w?.name ?? (slug === PERSONAL ? null : slug));
  const readOnly = w?.write === false;
  const shared = w ? w.workspace_kind !== "private" : slug !== PERSONAL;
  const kind = w?.workspace_kind === "company" ? "Company" : w?.workspace_kind === "shared" ? "Shared" : "Private";
  const [a, setA] = useState<WorkspaceAttention | null>(null);
  const { data: home } = useAttention();
  useEffect(() => {
    let live = true;
    const read = () => void readWorkspaceAttention(slug).then((x) => { if (live && x) setA(x); });
    read();
    const t = window.setInterval(() => { if (document.visibilityState === "visible") read(); }, REFRESH_MS);
    return () => { live = false; window.clearInterval(t); };
  }, [slug, home?.generated_at]);
  const extras = useWorkspaceExtras(slug);
  const [wide, setWide] = useState(false);
  useEffect(() => () => { fire(WORKSPACE_PAGE_WIDE_EVENT, { wide: false }); }, []);
  const [confirm, dialog] = useConfirm();
  const [filter, setFilter] = useState<"all" | "you" | "member" | "system">("all");
  const [dragging, setDragging] = useState<string | null>(null);
  const [inviting, setInviting] = useState(false);
  // S7 (UI 7aa): below the fixed header the page IS the README, rendered for this viewer by the server
  const readmePage = useReadmeFeature("readme_page");
  useEffect(() => {
    const on = (e: Event) => { if ((e as CustomEvent<{ workspace?: string }>).detail?.workspace === slug) setInviting(true); };
    window.addEventListener(INVITE_EVENT, on);
    return () => window.removeEventListener(INVITE_EVENT, on);
  }, [slug]);
  const cards = useMemo(() => (desktop?.cards_on ? (desktop.sections.find((s) => s.id === "cards")?.items ?? []) : [])
    .filter((i): i is CardItem => i.kind === "card" && i.workspaces.includes(slug)), [desktop, slug]);
  const stages = useMemo(() => [...new Set(cards.flatMap((c) => [...c.states, c.state]))], [cards]);
  const runNeed = async (n: Need) => {
    const plan = planNeedAct(n.act, n.text);
    if (plan.confirm && !(await confirm(plan.confirm))) return;
    await plan.run();
    const x = await readWorkspaceAttention(slug, { force: true }); if (x) setA(x);
  };
  const toggleWide = () => { const v = !wide; setWide(v); fire(WORKSPACE_PAGE_WIDE_EVENT, { wide: v }); };
  const live = a?.live_call ?? w?.live_call ?? null;
  const next = (a?.calls ?? []).filter((c) => c.state === "upcoming").sort((x, y) => x.at - y.at)[0];
  const upcomingNeed = (a?.needs ?? []).find((n) => n.kind === "upcoming");
  const needs = a?.needs ?? [];
  const commits = extras.commits.filter((c) => filter === "all" || c.kind === filter);
  const dropOn = (stage: string) => async (e: DragEvent) => {
    e.preventDefault();
    const card = cards.find((c) => c.id === dragging);
    setDragging(null);
    if (!card || card.state === stage || !card.can.transition) return;
    if (!(await confirm({ title: `Move “${card.title}”?`, consequence: `It moves to ${stage} for everyone on the card.`, confirmLabel: "Move it", tone: "primary" }))) return;
    await updateCard(card, { transition: stage });
  };
  const style = { "--wsp-h": String(wsHue(slug)), "--ws-h": String(wsHue(slug)) } as CSSProperties;
  const openReadme = () => openDocOnRight(slug, "README.md", `${name} · README`);
  const settings = useWorkspaceSettings();
  const policy = usePolicy();
  const mine = settings.of(slug);
  // shell2 (UI 7l, 7m, 7v): Waiting on you → Rooms → Agents and routines, every list on the kit's row
  const shell2 = useFeature("shell2");
  // shell2 (S7 browser check): the header's acts stay on ONE line — in a narrow panel (a phone sheet,
  // a narrow docked panel) New room and Schedule fold into ⋯, leaving New chat · ⋯
  const headRef = useRef<HTMLElement>(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const el = headRef.current;
    if (!shell2 || !el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setNarrow(e.contentRect.width < 400));
    ro.observe(el);
    return () => ro.disconnect();
  }, [shell2]);
  const folded = shell2 && narrow;
  // a room's last line names who spoke ("Maya: the venue is booked · 3 h ago") when the read carries it
  const roomLine = (r: { members: number; last_active: number; last_by?: string; last_line?: string }) => {
    const said = r.last_by ? `${nameOrYou(r.last_by)}${r.last_line ? `: ${r.last_line}` : ""}` : `${r.members} ${r.members === 1 ? "member" : "members"}`;
    return `${said}${r.last_active ? ` · ${agoText(r.last_active)}` : ""}`;
  };
  const agoText = (at: number) => { const x = ago(at); return x === "now" || x === "yesterday" ? x : `${x} ago`; };
  const runRoutine = async (r: { name: string; ref: string }) => {
    if (!(await confirm({ title: `Run “${r.name}” now?`, consequence: "It runs as you, with everything it is allowed to do.", confirmLabel: "Run it now", tone: "primary" }))) return;
    askAgent(`Run my routine ${r.ref.replace(/^agent:/, "")} now and tell me how it went.`, { display: `Run ${r.name} now` });
  };
  const fold = { collapsed: mine.collapsed, toggle: (id: string) => {
    if (settings.phase !== "ready") return;
    const next = mine.collapsed.includes(id) ? mine.collapsed.filter((x) => x !== id) : [...mine.collapsed, id];
    void setSettings(slug, { collapsed: next });
  } };
  // rooms in the person's own order (room_order), the rest newest first
  const rooms = (a?.rooms ?? []).slice().sort((x, y) => {
    const ix = mine.room_order.indexOf(x.session), iy = mine.room_order.indexOf(y.session);
    return (ix < 0 ? 1e9 : ix) - (iy < 0 ? 1e9 : iy) || y.last_active - x.last_active;
  });
  return (
    <article className="vx-wsp" style={style} data-wide={wide ? "" : undefined} data-workspace-page={slug} aria-label={`${name} — workspace`}>
      {/* 1 · HEADER */}
      <header ref={headRef} className="vx-wsp-head" data-folded={folded ? "" : undefined}>
        <div className="vx-wsp-id">
          <WsMark slug={slug} name={name} size={56} />
          <div className="vx-wsp-idt"><h1>{name}</h1>
            {/* shell2 (founder 2026-10-17): people first — named faces with presence beside the title,
                "N people" to the People section, Invite next to them; then who changed it last */}
            {shell2 && <div className="vx-wsp-meta vx-s2-wsp-people">{w && w.members.length > 0 && <PeopleLine members={w.members} max={5} />}
              {w && w.members.length > 0 && <button type="button" className="vx-s2-linkbtn" data-people-pill="" onClick={() => document.getElementById("wsp-people")?.scrollIntoView({ block: "start", behavior: "smooth" })}>{w.members.length} {w.members.length === 1 ? "person" : "people"}</button>}
              {shared && slug !== PERSONAL && !readOnly && <button type="button" className="vx-s2-linkbtn" data-invite="" onClick={() => setInviting(true)}>Invite</button>}</div>}
            {shell2 && lastChange(w?.last_activity as { line: string; at: number; by?: string } | null) && <p className="vx-s2-note vx-s2-wsp-last" data-last-change="">{kind}{readOnly ? " · read-only" : ""} · {lastChange(w?.last_activity as { line: string; at: number; by?: string } | null)}</p>}
            {!shell2 && <div className="vx-wsp-meta"><span className="vx-wsp-kind">{kind}{readOnly ? " · read-only" : ""}</span>
              {w && w.members.length > 0 && <span className="vx-wsp-faces">{w.members.slice(0, 5).map((m) => <i key={m.subject} title={m.name}>{m.initials}</i>)}{w.members.length > 5 && <i>+{w.members.length - 5}</i>}</span>}</div>}</div>
          <button type="button" className="vx-wsp-expand" title={wide ? "Narrow the panel" : "Expand: two columns"} aria-label={wide ? "Narrow the panel" : "Expand to two columns"} aria-pressed={wide} onClick={toggleWide}>
            {wide ? <Minimize2 size={14} aria-hidden /> : <Maximize2 size={14} aria-hidden />}</button>
        </div>
        <div className="vx-wsp-acts">
          {!readOnly && <>
            <button type="button" className="vx-kh-btn" onClick={() => { window.dispatchEvent(new CustomEvent(NEW_CHAT_AT_EVENT, { detail: { workspace: slug } })); }}><MessageSquare size={12} aria-hidden /> New chat</button>
            {!folded && <button type="button" className="vx-kh-btn" onClick={() => { window.dispatchEvent(new CustomEvent(NEW_ROOM_EVENT, { detail: { workspace: slug } })); }}><Hash size={12} aria-hidden /> New room</button>}
            {shared && !folded && <button type="button" className="vx-kh-btn" onClick={() => { window.dispatchEvent(new CustomEvent(NEW_CHAT_AT_EVENT, { detail: { workspace: slug, draft: `Schedule a call in ${name}: find a time that suits everyone and draft the invitation.` } })); }}><CalendarDays size={12} aria-hidden /> Schedule</button>}
            {shared && slug !== PERSONAL && !shell2 && <button type="button" className="vx-kh-btn" data-invite="" onClick={() => setInviting(true)}>Invite…</button>}
          </>}
          <Menu label={`More for ${name}`} variant="ghost" align="end" trigger={<MoreHorizontal size={15} aria-hidden />} items={[
            ...(folded && !readOnly ? [
              { key: "new-room", label: "New room", onSelect: () => { window.dispatchEvent(new CustomEvent(NEW_ROOM_EVENT, { detail: { workspace: slug } })); } },
              ...(shared ? [{ key: "schedule", label: "Schedule a call", onSelect: () => { window.dispatchEvent(new CustomEvent(NEW_CHAT_AT_EVENT, { detail: { workspace: slug, draft: `Schedule a call in ${name}: find a time that suits everyone and draft the invitation.` } })); } }] : []),
            ] : []),
            { key: "readme", label: readmePage ? "Edit README" : "Open README", separatorBefore: folded && !readOnly, onSelect: openReadme },
            ...(readmePage && slug !== PERSONAL ? [{ key: "copy-link", label: "Copy link", onSelect: () => { void copyReference({ kind: "workspace", id: slug, label: name }); } }] : []),
            ...(settings.phase === "ready" ? [
              { key: "pin", label: mine.pinned ? "Unpin from the top of home" : "Pin to the top of home", separatorBefore: true, onSelect: () => { void setSettings(slug, { pinned: !mine.pinned }); } },
              { key: "mute", label: mine.muted ? "Unmute" : "Mute", onSelect: () => { void setSettings(slug, { muted: !mine.muted }); } },
              ...(["all", "mentions", "none"] as const).map((n) => ({ key: `notify-${n}`, label: n === "all" ? "Notify me about everything" : n === "mentions" ? "Notify me only when mentioned" : "No notifications", checked: (policy.policy.rooms[`ws:${slug}`] ?? "mentions") === n, onSelect: () => { void savePolicy({ rooms: { [`ws:${slug}`]: n } }); } })),
              ...(slug !== PERSONAL ? [{ key: "hide", label: mine.hidden ? "Show on home again" : "Hide from home", onSelect: () => { void setSettings(slug, { hidden: !mine.hidden }); } }] : []),
            ] : []),
          ]} />
        </div>
      </header>

      {readmePage ? <section className="vx-wsp-readme" data-wsp-readme="" data-readme-page=""><ReadmeView slug={slug} /></section> :
      <FoldContext.Provider value={fold}><div className="vx-wsp-grid">
        {/* THE PAGE IS A STATUS BLOCK OVER THE README (founder ruling 2026-10-16): what needs the
            person and the recurring agents, at most about half the panel and scrolling inside
            itself; the README is the rest. Chats live in the sidebar; a call is reached from its room. */}
        {shell2 && (needs.length > 0 || rooms.length > 0 || (a?.routines?.length ?? 0) > 0 || (w?.members.length ?? 0) > 0) && <div className="vx-wsp-status vx-s2-wsp" data-wsp-status="">
          {needs.length > 0 && <Section id="needs" title="Waiting on you" count={needs.length}>
            {needs.map((n) => <RowNeed key={n.id} n={n} readOnly={readOnly} act={(x) => void runNeed(x)} />)}
          </Section>}
          {w && w.members.length > 0 && <WorkspacePeopleSection members={w.members} />}
          {rooms.length > 0 && <Section id="rooms" title="Rooms" count={rooms.length}>
            {rooms.map((r) => <ListRow key={r.session} size="md" strong leading={r.closed ? <Lock size={14} strokeWidth={1.75} /> : <Hash size={14} strokeWidth={1.75} />}
              title={r.name.replace(/^#/, "")} detail={roomLine(r)}
              meta={r.unread ? <RowCell w={52} align="end">{r.unread} new</RowCell> : undefined}
              onSelect={() => fire(OPEN_SESSION_EVENT, { session: r.session })} />)}
          </Section>}
          {(a?.routines?.length ?? 0) > 0 && <Section id="routines" title="Agents and routines" count={a!.routines!.length}>
            {a!.routines!.map((r) => {
              const session = (r as { session?: string | null }).session ?? null;
              // 7i: the whole row opens the routine's chat; Run now stays a secondary action
              return <ListRow key={r.ref} size="md" strong leading={<Bot size={14} strokeWidth={1.75} />} title={r.name}
                detail={`${r.state === "failed" ? "didn't finish" : r.state === "running" ? "running" : r.last_at ? `last ${agoText(r.last_at)}` : r.state}${r.next_at ? ` · next ${new Date(r.next_at * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : ""}`}
                onSelect={session ? () => fire(OPEN_SESSION_EVENT, { session }) : undefined}
                trailing={<>{r.runs.length > 0 && <RowCell w={72} align="end" hideNarrow><Spark runs={r.runs} /></RowCell>}
                  <RowCell w={104} align="end">{!readOnly && <Button variant="ghost" icon={<Play size={11} />} onClick={() => void runRoutine(r)}>Run now</Button>}</RowCell></>} />;
            })}
          </Section>}
        </div>}
        {!shell2 && (needs.length > 0 || (a?.routines?.length ?? 0) > 0) && <div className="vx-wsp-status" data-wsp-status="">
        {needs.length > 0 && <Section id="needs" title="Needs you" count={needs.length}>
          <ul className="vx-wsp-needs">{needs.map((n) => <NeedRow key={n.id} n={n} readOnly={readOnly} run={(x) => void runNeed(x)} />)}</ul>
        </Section>}
        {(a?.routines?.length ?? 0) > 0 && <Section id="routines" title="Agents and routines" count={a!.routines!.length}>
          <ul className="vx-wsp-routines">{a!.routines!.map((r) => {
            const session = (r as { session?: string | null }).session ?? null;
            const openChat = () => { if (session) fire(OPEN_SESSION_EVENT, { session }); };
            return <li key={r.ref} data-ok={r.state !== "failed" ? "" : undefined}>
            <div className="vx-wsp-rt-h"><Bot size={13} aria-hidden />
              {session ? <button type="button" className="vx-wsp-rt-name" aria-label={`Open ${r.name}`} onClick={openChat}><b>{r.name}</b></button> : <b>{r.name}</b>}
              <span className="vx-wsp-rt-s">{r.state === "failed" ? "⚠" : r.state === "running" ? "●" : "✓"} {r.last_at ? `${ago(r.last_at)} ago` : r.state}{r.next_at ? ` · next ${new Date(r.next_at * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : ""}</span></div>
            {r.state === "failed" && r.runs[0] && !r.runs[0].ok && <small>{humanStatus(r.runs[0].what)}</small>}
            <div className="vx-wsp-rt-f">{r.runs.length > 0 && <span role={session ? "button" : undefined} tabIndex={session ? 0 : undefined}
                onClick={openChat} onKeyDown={(e) => { if (e.key === "Enter") openChat(); }}><Spark runs={r.runs} /></span>}
              {!readOnly && <button type="button" className="vx-pb-act" onClick={() => void (async () => {
                if (!(await confirm({ title: `Run “${r.name}” now?`, consequence: "It runs as you, with everything it is allowed to do.", confirmLabel: "Run it now", tone: "primary" }))) return;
                askAgent(`Run my routine ${r.ref.replace(/^agent:/, "")} now and tell me how it went.`, { display: `Run ${r.name} now` });
              })()}><Play size={11} aria-hidden /> Run now</button>}</div>
          </li>; })}</ul>
        </Section>}
        </div>}

        {/* THE README — the rest of the page */}
        {extras.readme != null && <section className="vx-wsp-readme" data-wsp-readme="">
          <RichPage slug={slug === PERSONAL ? undefined : slug} path="README.md" md={extras.readme} /></section>}
      </div></FoldContext.Provider>}
      {dialog}
      {inviting && <InviteDialog open slug={slug} name={name} onClose={() => setInviting(false)} />}
    </article>
  );
}

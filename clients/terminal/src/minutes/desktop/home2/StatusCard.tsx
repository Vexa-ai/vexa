"use client";
/** A WORKSPACE ON THE HOME IS ITS STATUS CARD (#1783, R3 `home2`; approved mock, founder 2026-10-15:
 *  "collapsed view should show some helpful info like routines states that work there, things that
 *  need attention"; "no expanded bucket").
 *
 *    1 · identity, name, a role badge only when it is not yours, what needs YOU (N), people, "…";
 *    2 · needs you — at most two, highest first, each with its act in place; or the calm line
 *        ("All quiet · last: …") when nothing does;
 *    3 · working here — routines with live state, a live call with Join, rooms active.
 *
 *  A click anywhere on the card aims the home composer there (a workspace you can only read is shown,
 *  never written to) and opens the workspace's view in the RIGHT panel — documents never open in the
 *  centre (UI decision 7c). What needs you and what is working come from the attention read model
 *  (E1) exactly as the server sent them; nothing is decided here. */
import { workspaceDisplayName } from "../../vocabulary";
import { humanStatus } from "./humanStatus";
import { createReorder } from "./homeReorder";
import { toast } from "../../../ui-kit/primitives/Toast";
import { arrangeQuiet } from "../desktopApi";
import { useEffect, useMemo, useState, type CSSProperties, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { Video } from "lucide-react";
import type { WorkspaceItem } from "../../../platform/desktop";
import { CALL_START_EVENT, OPEN_MEETING_EVENT } from "../../../platform/events";
import { Menu } from "../../../ui-kit";
import { useConfirm } from "../../../ui-kit/primitives/Dialog";
import { Badges, fromInner, type TileDrag } from "../DesktopTiles";
import { planNeedAct, askAgent, resolveProposal } from "./acts";
import { NewWorkspaceDialog } from "./NewWorkspace";
import { workspaceAttention, useAttention, type LiveCall, type Need, type Work, type WorkspaceAttention } from "./attentionApi";
import { aimHome, toggleHomeTarget, useHomeTarget, wsHue, WsMark } from "./HomeTarget";
import { openWorkspaceView } from "./workspaceView";
import { homeOrder, setSettings, useWorkspaceSettings } from "./settingsApi";
import { useFeature } from "../../../platform/features";
import { PeopleLine, lastChange } from "../../shell2/WorkspacePeople";
import { nameOrYou } from "../../shell2/me";
import "./home2.css";

const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));

/** Run an item's act, confirming first when it reaches beyond the person's screen. */
export function useNeedAct(): [(n: Need) => Promise<void>, ReactNode] {
  const [confirm, dialog] = useConfirm();
  const run = async (n: Need) => {
    const plan = planNeedAct(n.act, n.text);
    if (plan.confirm && !(await confirm(plan.confirm))) return;
    await plan.run();
  };
  return [run, dialog];
}

/** shell2: "who waits on you" — the person first, when the server names one the sentence does not */
function whoLead(n: Need): string {
  const by = (n as { by?: unknown }).by ?? n.act.by;
  if (typeof by !== "string" || !by.trim() || n.text.includes(by)) return "";
  return `${nameOrYou(by.trim())} · `;
}

/** "Needs you": at most `max`, each with its act in place. */
export function NeedsList({ needs, act, max = 2 }: { needs: Need[]; act: (n: Need) => void; max?: number }) {
  return <ul className="vx-scard-needs" data-needs="">{needs.slice(0, max).map((n) => {
    const identity = n.act.source === "identity" && n.act.slug;
    return <li key={n.id} data-kind={n.kind} data-need={n.id}>
      {/* a proposed image is shown before it is used (mock 12b): the server serves the proposal */}
      {identity && <img className="vx-scard-prop" alt="" width={20} height={20}
        src={`/api/workspaces/${encodeURIComponent(String(n.act.slug))}/identity/icon?proposal=1`} />}
      <span className="vx-scard-need" title={humanStatus(n.text)}>{whoLead(n)}{humanStatus(n.text)}</span>
      <button type="button" className="vx-pb-act vx-pb-act-go" data-tile-inner="" onClick={(e) => { e.stopPropagation(); act(n); }}>{n.act.label}</button>
      {identity && <button type="button" className="vx-pb-act" data-tile-inner=""
        onClick={(e) => { e.stopPropagation(); void resolveProposal(n.act, false); }}>Decline</button>}
    </li>;
  })}</ul>;
}

const mark = (s: Work["state"]) => (s === "ok" ? "✓ " : s === "failed" ? "⚠ " : s === "running" ? "● " : "");

/** "Working here": routines with live state, a live call with Join, rooms active. */
export function WorkingChips({ live, a, onRoutine, maxRoutines }: { live: LiveCall | null; a: WorkspaceAttention; onRoutine?: (w: Work) => void; maxRoutines?: number }) {
  const work = a.working.filter((x) => x.kind !== "call");
  if (!live && !work.length && !a.rooms_active) return null;
  return <div className="vx-scard-work" data-working="">
    {live && <button type="button" className="vx-scard-chip" data-state="live" data-tile-inner=""
      onClick={(e) => { e.stopPropagation(); if (live.session) fire(CALL_START_EVENT, { session: live.session }); else if (live.meeting) fire(OPEN_MEETING_EVENT, { ref: String(live.meeting) }); }}>
      <span className="vx-pb-dot" aria-hidden /> {live.line ?? "Call"}{typeof live.people === "number" ? ` · ${live.people}` : ""} · <b>Join</b> <Video size={11} aria-hidden /></button>}
    {work.filter((x) => x.kind === "routine").slice(0, maxRoutines ?? Infinity).map((x) => <button type="button" key={x.ref ?? x.label} className="vx-scard-chip" data-state={x.state} data-tile-inner=""
      title="What it did" onClick={(e) => { e.stopPropagation(); onRoutine?.(x); }}>
      {x.label} · {mark(x.state)}{x.when ?? x.state}</button>)}
    {a.rooms_active > 0 && <span className="vx-scard-chip">{a.rooms_active} {a.rooms_active === 1 ? "room" : "rooms"} active</span>}
  </div>;
}

export function StatusCard({ w: given, drag }: { w: WorkspaceItem; drag: TileDrag }) {
  const shell2 = useFeature("shell2");
  const slug = given.slug || "personal";
  // "My desk" (UI 7y, shell2): the personal workspace's display name, whatever the server called it
  const w = slug === "personal" ? { ...given, name: workspaceDisplayName(slug, given.name) } : given;
  const { data } = useAttention();
  const a = workspaceAttention(data, slug);
  const [act, dialog] = useNeedAct();
  const isTarget = useHomeTarget() === slug;
  const live = a.live_call ?? w.live_call ?? null;
  const readOnly = w.write === false;
  const settings = useWorkspaceSettings();
  const settingsOn = settings.phase === "ready";
  const mine = settings.of(slug);
  const activate = () => {
    if (!readOnly) toggleHomeTarget(slug);
    void openWorkspaceView(w);
  };
  const click = (e: MouseEvent<HTMLElement>) => { if (fromInner(e)) return; activate(); };
  const key = (e: KeyboardEvent<HTMLElement>) => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); activate(); } };
  const calm = a.last ?? w.last_activity?.line ?? null;
  return (
    <article className="vx-sc-card" draggable data-desk-tile={w.ref} data-status-card={slug} role="button" tabIndex={0}
      data-target={isTarget && !readOnly ? "" : undefined} data-readonly={readOnly ? "" : undefined}
      style={{ "--ws-h": String(wsHue(slug)) } as CSSProperties}
      aria-label={`${w.name}${a.needs.length ? ` — ${a.needs.length} need you` : ""}. ${readOnly ? "Show what is here" : "Show what is here, and write here from home"}`}
      onClick={click} onKeyDown={key} {...drag}>
      <WsMark slug={slug} name={w.name} size={40} />
      <div className="vx-scard-main">
        <div className="vx-scard-l1">
          <b className="vx-scard-name">{w.name}</b>
          {w.access && w.access !== "owner" && <span className="vx-scard-role">{w.access}</span>}
          {mine.pinned && <span className="vx-scard-role" data-pinned="">pinned</span>}
          {mine.muted && <span className="vx-scard-role" data-muted="">muted</span>}
          <span className="vx-pb-bar-gap" />
          {a.needs.length > 0 && <span className="vx-scard-n" title={`${a.needs.length} need you`} data-need-count={a.needs.length}><i aria-hidden />{a.needs.length}</span>}
          <span data-tile-inner="" className="vx-scard-more">
            <Menu label={`More for ${w.name}`} variant="ghost" align="end" trigger={<span aria-hidden>⋯</span>} items={[
              ...(!readOnly ? [{ key: "write", label: "Write here from home", onSelect: () => aimHome(slug) },
                { key: "ask", label: "Ask about this workspace", onSelect: () => { aimHome(slug); askAgent(`[[workspace:${slug}]] What is going on here?`, { draft: true }); } }] : []),
              { key: "open", label: "Open", onSelect: () => { void openWorkspaceView(w); } },
              // your own view of this workspace: the CRM member_of relation, written by crm-api
              ...(settingsOn ? [
                { key: "pin", label: mine.pinned ? "Unpin from the top" : "Pin to the top", separatorBefore: true, onSelect: () => { void setSettings(slug, { pinned: !mine.pinned }); } },
                { key: "mute", label: mine.muted ? "Unmute" : "Mute", onSelect: () => { void setSettings(slug, { muted: !mine.muted }); } },
                ...(slug !== "personal" ? [{ key: "hide", label: "Hide from home", onSelect: () => { void setSettings(slug, { hidden: true }); } }] : []),
              ] : []),
            ]} />
          </span>
        </div>
        {/* shell2: the people first — named faces with presence — then what waits, then who changed it */}
        {shell2 && w.members.length > 0 && <div className="vx-s2-scard-people" data-card-people=""><PeopleLine members={w.members} max={3} /></div>}
        {/* ONE STATUS LINE (founder 2026-10-16): the most important thing, or that nothing needs you */}
        {a.needs.length > 0
          ? <NeedsList needs={a.needs} act={(n) => void act(n)} max={1} />
          : live ? null : <div className="vx-scard-calm" data-calm="">Nothing needs you</div>}
        <WorkingChips live={live} a={a} onRoutine={() => { void openWorkspaceView(w); }} maxRoutines={2} />
        {/* shell2 (founder 2026-10-17: "a workspace is about the people around it"): named faces with
            presence first, then who changed it last — counts come second */}
        {shell2 && lastChange(w.last_activity as { line: string; at: number; by?: string } | null) && <div className="vx-scard-calm vx-s2-lastchange" data-last-change="">{lastChange(w.last_activity as { line: string; at: number; by?: string } | null)}</div>}
        {!shell2 && w.members.length > 0 && <div className="vx-scard-foot" data-card-foot=""><span className="vx-scard-people" data-tile-inner=""><Badges people={w.members} /></span></div>}
      </div>
      {dialog}
    </article>
  );
}

/** A NEW WORKSPACE IS A CONVERSATION (founder 2026-10-16, UI 7g): the agent asks what it needs, one
 *  inline card at a time, and creates nothing until the person presses Create on its confirm card. */
export const NEW_WORKSPACE_KICK = [
  "Let's set up a new workspace. Ask me what it is for first, then only what you still need — one question at a time,",
  "each as an inline card: a fenced ```ask block holding JSON {\"id\", \"question\", \"chips\": [..], \"free\": true}.",
  "Ask who it is for with chips Just me / Me and others. Infer its name and its company or project site from my",
  "answers and propose them as a card (\"Name it …?\" with Yes / Change). End with ONE confirm card whose summary lists the",
  "name, who can see it, the logo you would use and the people to invite, with primary \"Create workspace\" and secondary",
  "\"Change something\". Create nothing until I press Create: then workspace_new (shared when I said others),",
  "workspace_icon_set with propose: true, and for each person I confirmed workspace_member_add (an account here) or",
  "workspace_invite (anyone else); then open its page on the right with open_page. Say what you did in one line.",
].join(" ");

/** The home's workspaces: pinned first, the rest as arranged; those hidden from home on one line with
 *  Show again. The order and the hiding are the person's own settings (CRM `member_of`). */
export function HomeCards({ items, tileDrag: _tileDrag }: { items: WorkspaceItem[]; tileDrag: (ref: string) => TileDrag }) {
  const settings = useWorkspaceSettings();
  const { shown: served, hidden } = homeOrder(items, settings.of);
  const [making, setMaking] = useState(false);
  // TRELLO-LIGHT REORDER (founder 2026-10-16): local at once, saved quietly after the burst
  const [order, setOrder] = useState<string[]>(() => served.map((i) => i.ref));
  const [dragging, setDragging] = useState<string | null>(null);
  const reorder = useMemo(() => createReorder({
    onChange: setOrder,
    onFail: () => toast("Couldn't save the new order"),
    save: (next) => arrangeQuiet(next.slice(0, -1).map((id, i) => ({ op: "move", id, before: next[i + 1] })).reverse()),
  }), []);
  const servedKey = served.map((i) => i.ref).join("|");
  useEffect(() => { reorder.set(served.map((i) => i.ref)); }, [servedKey]);  // eslint-disable-line react-hooks/exhaustive-deps
  const byRef = new Map(served.map((i) => [i.ref, i]));
  const shown = [...order.filter((r) => byRef.has(r)).map((r) => byRef.get(r)!), ...served.filter((i) => !order.includes(i.ref))];
  const drag = (ref: string): TileDrag => ({
    onDragStart: (e) => { e.stopPropagation(); e.dataTransfer.effectAllowed = "move"; setDragging(ref); },
    onDragOver: (e) => { if (!dragging) return; e.preventDefault(); if (dragging !== ref) reorder.move(dragging, ref); },
    onDrop: (e) => { e.preventDefault(); e.stopPropagation(); setDragging(null); },
  });
  const keys = (ref: string) => (e: KeyboardEvent<HTMLDivElement>) => {
    if (!e.altKey) return;
    if (e.key === "ArrowUp" || e.key === "ArrowLeft") { e.preventDefault(); reorder.move(ref, -1); }
    if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); reorder.move(ref, 1); }
  };
  return <div className="vx-pb-list" role="list" data-home-grid="">
    <div className="vx-nw-bar"><button type="button" className="vx-pb-act vx-pb-act-go" data-new-workspace="" onClick={() => askAgent(NEW_WORKSPACE_KICK, { display: "Set up a new workspace" })}>+ New workspace</button></div>
    {shown.map((it) => <div role="listitem" key={it.ref} className="vx-desk-cell vx-hc-cell" data-dragging={dragging === it.ref ? "" : undefined}
      onKeyDown={keys(it.ref)} onDragEnd={() => setDragging(null)}><StatusCard w={it} drag={drag(it.ref)} /></div>)}
    {hidden.length > 0 && <div className="vx-scard-calm" data-hidden-from-home="">Hidden from home: {hidden.map((h, i) => <span key={h.ref}>{i > 0 ? ", " : ""}{h.name}{" "}
      <button type="button" className="vx-pb-act" onClick={() => { void setSettings(h.slug || "personal", { hidden: false }); }}>Show again</button></span>)}</div>}
  </div>;
}

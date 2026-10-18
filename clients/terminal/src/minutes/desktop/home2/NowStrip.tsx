"use client";
/** NOW (#1783, R3 `home2`; approved mock): the top of the home says what is happening across every
 *  workspace — calls live now (Join), the next call within a day (Prepare), and the first things
 *  that wait on you. One read model (E1) feeds it and the status cards, so they always agree. Nothing
 *  is shown when nothing is happening. */
import { Video } from "lucide-react";
import type { WorkspaceItem } from "../../../platform/desktop";
import { CALL_START_EVENT, OPEN_MEETING_EVENT } from "../../../platform/events";
import { useNeedAct } from "./StatusCard";
import { humanStatus } from "./humanStatus";
import { useAttention, workspaceAttention, type Need } from "./attentionApi";
import { useWorkspaceSettings } from "./settingsApi";
import { useFeature } from "../../../platform/features";
import { ListRow, RowActions, StatusDot } from "../../../ui-kit";
import { NeedRow } from "../../shell2/NeedRow";
import { openCentre } from "../../shell2/centre";
import { inboxCount } from "../../shell2/inbox";
import { NowCards, useWaitingCount } from "../../cards";
import "./home2.css";

const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));
const NOW_NEEDS = 3;

export function NowStrip({ workspaces }: { workspaces: WorkspaceItem[] }) {
  const { data } = useAttention();
  // S5: once the card Inbox answers, cards come from its one store (the same count as the Inbox and a room badge)
  const cards = useWaitingCount();
  const [act, dialog] = useNeedAct();
  // a workspace the person muted or hid from home does not reach the top of the home
  const settings = useWorkspaceSettings();
  const quiet = (slug: string) => settings.of(slug).muted || settings.of(slug).hidden;
  const nameOf = (slug: string) => workspaces.find((w) => (w.slug || "personal") === slug)?.name ?? null;
  const live = workspaces.map((w) => ({ w, call: workspaceAttention(data, w.slug || "personal").live_call ?? w.live_call ?? null }))
    .filter((x) => x.call);
  const all: Need[] = (data?.needs ?? []).filter((n) => nameOf(n.workspace) !== null && !quiet(n.workspace) && (cards === null || n.kind !== "card"));
  const upcoming = all.filter((n) => n.kind === "upcoming").slice(0, 1);
  const rest = all.filter((n) => n.kind !== "upcoming").slice(0, NOW_NEEDS);
  const shell2 = useFeature("shell2");
  if (!live.length && !upcoming.length && !rest.length && !cards) return null;
  const join = (call: NonNullable<(typeof live)[number]["call"]>) => {
    if (call.session) fire(CALL_START_EVENT, { session: call.session }); else if (call.meeting) fire(OPEN_MEETING_EVENT, { ref: String(call.meeting) });
  };
  // shell2 (UI 7m, 7r): the same calm rows as the Inbox — kind icon, title, muted tail, one outlined
  // verb in a fixed column, ⋯ — and "Inbox · N" under them. Nothing is filled; Join is outlined.
  if (shell2) return (
    <section className="vx-pb-now vx-s2-now" aria-label="Now" data-now-strip="">
      <h3 className="vx-kh-h">Now</h3>
      {live.map(({ w, call }) => <div key={w.ref} className="vx-s2-need" data-live="">
        <ListRow size="md" strong leading={<StatusDot tone="success" label="" />} title={`Call now · ${call!.line ?? w.name}`}
          detail={`${w.name}${typeof call!.people === "number" ? ` · ${call!.people} people` : ""}`} onSelect={() => join(call!)}
          trailing={<RowActions verb={{ label: "Join", onClick: () => join(call!), icon: <Video size={12} /> }} />} /></div>)}
      {cards ? <NowCards /> : null}
      {[...upcoming, ...rest].map((n) => <NeedRow key={n.id} n={n} where={nameOf(n.workspace) ?? undefined} act={(x) => void act(x)} />)}
      {inboxCount(all) > rest.length && <button type="button" className="vx-s2-now-more" onClick={() => openCentre("inbox")}>Inbox · {inboxCount(all)}</button>}
      {dialog}
    </section>
  );
  return (
    <section className="vx-pb-now" aria-label="Now" data-now-strip="">
      <h3 className="vx-kh-h">Now</h3>
      {live.map(({ w, call }) => <div key={w.ref} className="vx-pb-nowrow" data-live="">
        <span className="vx-pb-dot" aria-hidden />
        <span><b>Call now · {call!.line ?? w.name}</b><small>{w.name}{typeof call!.people === "number" ? ` · ${call!.people} people` : ""}</small></span>
        <button type="button" className="vx-kh-btn vx-kh-btn-primary" onClick={() => {
          if (call!.session) fire(CALL_START_EVENT, { session: call!.session }); else if (call!.meeting) fire(OPEN_MEETING_EVENT, { ref: String(call!.meeting) });
        }}><Video size={12} aria-hidden /> Join</button></div>)}
      {[...upcoming, ...rest].map((n) => <div key={n.id} className="vx-pb-nowrow" data-need={n.id} data-kind={n.kind}>
        {n.kind === "upcoming" ? <Video size={14} aria-hidden /> : <i className="vx-pb-nowmark" aria-hidden><i /></i>}
        <span><b>{humanStatus(n.text)}</b><small>{nameOf(n.workspace)}</small></span>
        <button type="button" className="vx-kh-btn" onClick={() => void act(n)}>{n.act.label}</button></div>)}
      {cards ? <NowCards /> : null}
      {dialog}
    </section>
  );
}

"use client";
/** NOW (#1783, R3 `home2`; approved mock): the top of the home says what is happening across every
 *  workspace — calls live now (Join), the next call within a day (Prepare), and the first things
 *  that wait on you. One read model (E1) feeds it and the status cards, so they always agree. Nothing
 *  is shown when nothing is happening. */
import { useLiveMeetings } from "../../../surfaces/liveMeetings";
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
import { useEffect, useState } from "react";
import { getCall } from "../../../surfaces/callsApi";
import { OPEN_SESSION_EVENT } from "../../../platform/events";
import { useActiveCall } from "../../callState";
import { elapsed, inNowLine } from "../../shell2/callNames";
import { isMe } from "../../shell2/me";
import { nowLiveRows, type NowLiveRow } from "./nowLive";
import { HomePrepare, nextPlanned } from "./HomePrepare";
import "./home2.css";

/** ONE LIVE MEETING ON HOME (founder O1, 2026-10-18): its name, who is in, how long, and Rejoin / Open —
 *  or "You're in this call" with Open when this tab is already connected (the dock holds the controls). */
function NowLive({ r }: { r: NowLiveRow }) {
  const [inNow, setInNow] = useState<string[] | null>(null);
  const [, tick] = useState(0);
  useEffect(() => {
    let on = true;
    const read = () => { if (r.vexa && r.session) void getCall(r.session).then((c) => { if (on) setInNow((c.call?.in_now ?? []).map((p) => p.name || p.subject).filter(Boolean)); }).catch(() => undefined); };
    read();
    const poll = window.setInterval(read, 10_000);
    const clock = window.setInterval(() => tick((x) => x + 1), 1000);
    return () => { on = false; window.clearInterval(poll); window.clearInterval(clock); };
  }, [r.vexa, r.session]);
  const names = inNow ?? r.people;
  const who = names.length ? inNowLine(names, (n) => isMe(n)) : typeof r.count === "number" ? `${r.count} in now` : "";
  const open = () => { if (r.inThis && r.session) fire(OPEN_SESSION_EVENT, { session: r.session }); else if (r.meetingId) fire(OPEN_MEETING_EVENT, { ref: r.meetingId }); else if (r.session) fire(OPEN_SESSION_EVENT, { session: r.session }); };
  const rejoin = () => { if (r.session) fire(CALL_START_EVENT, { session: r.session }); else open(); };
  const verb = r.inThis || !r.vexa ? { label: "Open", onClick: open } : { label: "Rejoin", onClick: rejoin, icon: <Video size={12} /> };
  const detail = [r.inThis ? "You're in this call" : "", who, r.sinceMs ? elapsed(r.sinceMs) : "", r.where ?? ""].filter(Boolean).join(" · ");
  return <div className="vx-s2-need" data-live="" data-live-now={r.key} data-in-this={r.inThis ? "" : undefined}>
    <ListRow size="md" strong leading={<StatusDot tone="success" label="" />} title={r.name} detail={detail || undefined} onSelect={open}
      trailing={<RowActions verb={verb} />} /></div>;
}

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
  // EVERY LIVE MEETING (founder O1): the meetings store and the tiles, merged by meeting (`nowLive`)
  const meetings = useLiveMeetings();
  const { current: inCall } = useActiveCall();
  const liveRows = nowLiveRows(live.map(({ w, call }) => ({ session: call!.session, meeting: call!.meeting, line: call!.line, people: call!.people, since: call!.since, where: w.name })),
    meetings, inCall ? { session: inCall.session, meetingId: inCall.join.call.meeting_id } : null);
  const all: Need[] = (data?.needs ?? []).filter((n) => nameOf(n.workspace) !== null && !quiet(n.workspace) && (cards === null || n.kind !== "card"));
  const shell2 = useFeature("shell2");
  // PREPARE FROM HOME (`home_prepare`, shell2): the next planned meeting heads the strip with Prepare,
  // in place of the upcoming-call need (one row per meeting, never two)
  const prepOn = useFeature("home_prepare") && shell2;
  const planned = nextPlanned(meetings);
  const upcoming = prepOn ? [] : all.filter((n) => n.kind === "upcoming").slice(0, 1);
  const rest = all.filter((n) => n.kind !== "upcoming").slice(0, NOW_NEEDS);
  if (!liveRows.length && !upcoming.length && !rest.length && !cards && !(prepOn && planned)) return null;
  // shell2 (UI 7m, 7r): the same calm rows as the Inbox — kind icon, title, muted tail, one outlined
  // verb in a fixed column, ⋯ — and "Inbox · N" under them. Nothing is filled; Join is outlined.
  if (shell2) return (
    <section className="vx-pb-now vx-s2-now" aria-label="Now" data-now-strip="">
      <h3 className="vx-kh-h">Now</h3>
      {prepOn && <HomePrepare />}
      {liveRows.map((r) => <NowLive key={r.key} r={r} />)}
      {cards ? <NowCards /> : null}
      {[...upcoming, ...rest].map((n) => <NeedRow key={n.id} n={n} where={nameOf(n.workspace) ?? undefined} act={(x) => void act(x)} />)}
      {inboxCount(all) > rest.length && <button type="button" className="vx-s2-now-more" onClick={() => openCentre("inbox")}>Inbox · {inboxCount(all)}</button>}
      {dialog}
    </section>
  );
  return (
    <section className="vx-pb-now" aria-label="Now" data-now-strip="">
      <h3 className="vx-kh-h">Now</h3>
      {liveRows.map((r) => <NowLive key={r.key} r={r} />)}
      {[...upcoming, ...rest].map((n) => <div key={n.id} className="vx-pb-nowrow" data-need={n.id} data-kind={n.kind}>
        {n.kind === "upcoming" ? <Video size={14} aria-hidden /> : <i className="vx-pb-nowmark" aria-hidden><i /></i>}
        <span><b>{humanStatus(n.text)}</b><small>{nameOf(n.workspace)}</small></span>
        <button type="button" className="vx-kh-btn" onClick={() => void act(n)}>{n.act.label}</button></div>)}
      {cards ? <NowCards /> : null}
      {dialog}
    </section>
  );
}

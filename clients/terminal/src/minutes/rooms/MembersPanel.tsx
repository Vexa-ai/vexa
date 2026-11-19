"use client";
/** THE MEMBERS PANEL (0.13.4 S4; sharing-tagging §8; mock panel "members" / "invite").
 *
 *  Opened from the header's people pill, as the right sheet (7d: a click never replaces the chat).
 *    · In #room — each member with how they got there and whether they opened it (a member who never
 *      did shows a faded face); an OPEN room's members are its workspace's (D36), said in the footer;
 *    · Invited, not on Vexa yet — with when the invite runs out;
 *    · In the workspace, not here — a closed room only, each with Add (the same card, no message).
 *  Invite opens the same picker as a panel: a name or an address; each pick becomes the tag card.
 *  Names only, never another person's address (security review M1). */
import { PersonChip } from "../shell2/PersonChip";
import { useEffect, useState } from "react";
import { UserPlus, X } from "lucide-react";
import { Identity } from "../../ui-kit";
import { Sheet } from "../../ui-kit/layout/Sheet";
import { formatDate } from "../../ui-kit/format/date";
import { openRoomOfTwo, readRoomPeople, tagPerson, type RoomPeople, type TagAnswer } from "../../surfaces/roomPeopleApi";
import { OPEN_SESSION_EVENT } from "../../platform/events";

/** "Message <person>" (7hh): the room of two with them, made once; opened in the centre. */
export async function messagePerson(subject: string): Promise<string> {
  const r = await openRoomOfTwo(subject);
  window.dispatchEvent(new CustomEvent(OPEN_SESSION_EVENT, { detail: { session: r.session_id } }));
  return r.session_id;
}
import { TagCard } from "../../surfaces/TagCard";
import "./roomPeople.css";

export function MembersPanel({ session, label, open, onClose, startWith = "members" }: {
  session: string; label: string; open: boolean; onClose: () => void; startWith?: "members" | "invite";
}) {
  const [data, setData] = useState<RoomPeople | null>(null);
  const [err, setErr] = useState("");
  const [mode, setMode] = useState<"members" | "invite">(startWith);
  const [query, setQuery] = useState("");
  const [card, setCard] = useState<TagAnswer | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => { setMode(startWith); }, [startWith, open]);
  useEffect(() => {
    if (!open) return;
    let live = true;
    readRoomPeople(session).then((d) => { if (live) { setData(d); setErr(""); } })
      .catch(() => { if (live) setErr("Couldn't read who is here."); });
    return () => { live = false; };
  }, [session, open, reload]);
  const pick = async (ref: { subject?: string; email?: string; name?: string }) => {
    setErr("");
    try { setCard(await tagPerson(session, ref)); } catch { setErr("Couldn't check that person. Try again."); }
  };
  const room = `#${label}`;
  const openRoom = data?.visibility === "open";
  const looksLikeEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(query.trim());
  return (
    <Sheet form="overlay" open={open} onClose={onClose} side="right" width={380} label={`Members of ${room}`} dataPane="room-members">
      <aside className="vx-mp" aria-label={`Members of ${room}`} data-panel={mode}>
        <div className="vx-mp-h">
          <b>{mode === "invite" ? `Invite to ${room}` : `${room} · members`}</b>
          <button type="button" className="vx-mp-x" aria-label="Close" onClick={onClose}><X size={14} aria-hidden /></button>
        </div>
        {mode === "members" && !data?.pair && (
          <button type="button" className="vx-btn" data-variant="primary" data-invite="" onClick={() => { setMode("invite"); setCard(null); }}>
            <UserPlus size={13} aria-hidden /> Invite to {room}</button>)}
        {err && <p className="vx-mp-err" role="alert">{err}</p>}
        {mode === "invite" && <>
          <label className="vx-mp-search"><span className="vx-sr">Name or email</span>
            <input value={query} placeholder="Name or email" aria-label="Name or email" onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && looksLikeEmail) void pick({ email: query.trim() }); }} /></label>
          <p className="vx-mp-lede">Invite is the same as @mentioning someone, without writing a message. Each pick becomes the same card.</p>
          {looksLikeEmail && <button type="button" className="vx-mp-row vx-mp-pick" onClick={() => void pick({ email: query.trim() })}>
            <span className="vx-mp-mail" aria-hidden>@</span><span><b>{query.trim()}</b><small>Invite by email</small></span></button>}
          {(data?.workspace_others ?? []).filter((o) => !query || o.name.toLowerCase().includes(query.toLowerCase())).map((o) => (
            <button key={o.principal} type="button" className="vx-mp-row vx-mp-pick" onClick={() => void pick({ subject: o.principal, name: o.name })}>
              <Identity kind="person" id={o.principal} name={o.name} size={26} /><span><b>{o.name}</b><small>in {data?.workspace_name} · add</small></span></button>))}
          {card && <TagCard session={session} answer={card} onDone={() => setReload((n) => n + 1)} />}
          {card?.case === "member" && <p className="vx-mp-lede">{card.name} is already in {room}.</p>}
          <button type="button" className="vx-btn" data-variant="ghost" onClick={() => setMode("members")}>Back to members</button>
        </>}
        {mode === "members" && data && <>
          <h4 className="vx-mp-sec">In {room} · {data.members.filter((x) => x.kind === "human").length}</h4>
          {data.members.filter((x) => x.kind === "human").map((x) => (
            <div key={x.principal} className="vx-mp-row" data-pending={x.opened ? undefined : ""}>
              <PersonChip person={{ name: x.name, subject: x.principal }}><Identity kind="person" id={x.principal} name={x.name} size={26} /></PersonChip>
              <span><b><PersonChip person={{ name: x.name, subject: x.principal }}>{x.name}</PersonChip>{x.principal === data.you ? " (you)" : ""}</b>
                <small>{[x.role === "owner" ? "owner" : x.derived ? `in ${data.workspace_name}` : "member",
                  x.added_by_name && x.role !== "owner" ? `added by ${x.added_by_name}` : "",
                  x.opened ? `opened ${formatDate(new Date(x.opened * 1000))}` : "not opened yet"].filter(Boolean).join(" · ")}</small></span>
              {x.principal !== data.you && !data.pair && (
                <button type="button" className="vx-btn" data-variant="ghost" data-message={x.principal}
                  onClick={() => { void messagePerson(x.principal).then(onClose).catch(() => setErr(`Couldn't open a conversation with ${x.name}.`)); }}>Message</button>)}
            </div>))}
          {data.invited.length > 0 && <>
            <h4 className="vx-mp-sec">Invited · not on Vexa yet</h4>
            {data.invited.map((i) => (
              <div key={i.invite_id} className="vx-mp-row" data-pending="">
                <span className="vx-mp-mail" aria-hidden>@</span>
                <span><b>{i.label}</b><small>invited by {i.by} · runs out {formatDate(new Date(i.expires_at * 1000))}</small></span>
              </div>))}
          </>}
          {data.workspace_others.length > 0 && <>
            <h4 className="vx-mp-sec">In {data.workspace_name}, not here · {data.workspace_others.length}</h4>
            {data.workspace_others.map((o) => (
              <div key={o.principal} className="vx-mp-row">
                <Identity kind="person" id={o.principal} name={o.name} size={26} />
                <span><b>{o.name}</b></span>
                <button type="button" className="vx-btn" data-variant="ghost" onClick={() => { setMode("invite"); void pick({ subject: o.principal, name: o.name }); }}>Add</button>
              </div>))}
          </>}
          <p className="vx-mp-foot">{data.pair ? "A room of two: just the two of you. Vexa here has no access to either person's own data."
            : openRoom ? `Open room: every ${data.workspace_name} member is in it and can open it. Adding someone tells them; joining by themselves doesn't.`
              : "Closed room: only the people listed here see it. Adding someone gives them its history."}</p>
        </>}
      </aside>
    </Sheet>
  );
}

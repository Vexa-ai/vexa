"use client";
/** SHARE = SEND A REFERENCE (PRD 0.13.4 D19; UI 7ff; coordinator's simplification 2026-10-17).
 *
 *  For ONE object (a meeting, page, card, room or message). The dialog is only:
 *    · "Send to…" — search a room or a person;
 *    · the object exactly as it will appear (its chip);
 *    · ONE quiet line, only when access differs ("Maya can't open this: it's in your desk. Share anyway?");
 *    · Send.
 *  No "view as recipient", no explanation. Sending posts the reference as a message (no agent turn);
 *  it grants nothing — each recipient sees what their own access allows (a request-access card when
 *  they cannot). Lists are never shared here: they get Copy link (the recipient sees their own list).
 *  A person is reached through the room of two with them (S4's pair room; `pairRoomFor` is the seam). */
import { useEffect, useMemo, useState } from "react";
import { Button } from "../../ui-kit/primitives/Button";
import { Dialog } from "../../ui-kit/primitives/Dialog";
import { toast } from "../../ui-kit/primitives/Toast";
import { RefChip } from "../../ui-kit/RefChip";
import { refChip } from "../../ui-kit/universalRefs";
import { listSessions, type SessionSummary } from "../../surfaces/sessionsApi";
import { loadPeople, usePeople } from "../desktop/cardsApi";
import type { RefTarget } from "../../surfaces/reference";

export type ShareTarget = { kind: "room"; id: string; name: string } | { kind: "person"; id: string; name: string };

/** The room of two with a person, or null when rooms of two are not here yet (S4). */
export async function pairRoomFor(person: string, f: typeof fetch = fetch): Promise<string | null> {
  try {
    const r = await f("/api/rooms/pair", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ person }) });
    if (!r.ok) return null;
    const d = (await r.json()) as { session?: string; session_id?: string };
    return d.session_id || d.session || null;
  } catch { return null; }
}

/** Post the reference into a room as one message (no agent turn). */
export async function sendReference(session: string, chip: string, f: typeof fetch = fetch): Promise<boolean> {
  try {
    const r = await f(`/api/sessions/${encodeURIComponent(session)}/messages`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: chip }) });
    return r.ok;
  } catch { return false; }
}

/** Who at the target could not open it — one line, or nothing. */
export async function accessLine(chip: string, t: ShareTarget, f: typeof fetch = fetch): Promise<string | null> {
  try {
    const r = await f("/api/refs/audience", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ref: chip, ...(t.kind === "room" ? { room: t.id } : { person: t.id }) }) });
    if (!r.ok) return null;
    const d = (await r.json()) as { cannot?: string[]; cannot_count?: number; where?: string; everyone?: boolean };
    if (d.everyone || !d.cannot_count) return null;
    const who = t.kind === "person" ? t.name.split(" ")[0] : d.cannot_count === 1 ? d.cannot?.[0] ?? "Someone" : `${d.cannot_count} people`;
    return `${who} can't open this: it's in ${d.where || "a place they are not in"}. Share anyway?`;
  } catch { return null; }
}

export function ShareDialog({ object, onClose }: { object: RefTarget; onClose: () => void }) {
  const chip = refChip(object.kind, object.id, undefined, object.anchor);
  const [q, setQ] = useState("");
  const [rooms, setRooms] = useState<SessionSummary[]>([]);
  const { people } = usePeople();
  const [to, setTo] = useState<ShareTarget | null>(null);
  const [line, setLine] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  useEffect(() => {
    void loadPeople();
    void listSessions().then((s) => setRooms(s.filter((x) => (x.participants ?? 1) > 1 && x.role !== "viewer"))).catch(() => setRooms([]));
  }, []);
  useEffect(() => {
    let live = true;
    setLine(null);
    if (to) void accessLine(chip, to).then((l) => { if (live) setLine(l); });
    return () => { live = false; };
  }, [to, chip]);
  const options = useMemo<ShareTarget[]>(() => {
    const n = q.trim().toLowerCase();
    const rs: ShareTarget[] = rooms.map((r) => ({ kind: "room", id: String(r.session_id || r.session), name: String(r.title || r.session) }));
    const ps: ShareTarget[] = people.map((p) => ({ kind: "person", id: p.subject, name: p.name }));
    return [...rs, ...ps].filter((o) => !n || o.name.toLowerCase().includes(n)).slice(0, 8);
  }, [q, rooms, people]);

  const send = async () => {
    if (!to) return;
    setSending(true);
    try {
      const session = to.kind === "room" ? to.id : await pairRoomFor(to.id);
      if (!session) { toast(`${to.name.split(" ")[0]} has no room with you yet`); return; }
      if (await sendReference(session, chip)) { toast(`Sent to ${to.name}`); onClose(); }
      else toast("Couldn't send it just now");
    } finally { setSending(false); }
  };

  return (
    <Dialog open onClose={onClose} title="Send to…" footer={
      <Button variant="primary" disabled={!to || sending} loading={sending} onClick={() => void send()}>Send</Button>}>
      <div className="vx-share" data-share="">
        <input className="vx-share-q" aria-label="Send to a room or a person" placeholder="A room or a person" value={to ? to.name : q}
          onChange={(e) => { setTo(null); setQ(e.target.value); }} autoFocus />
        {!to && options.length > 0 && <ul className="vx-share-opts" role="listbox">{options.map((o) => (
          <li key={`${o.kind}:${o.id}`}><button type="button" role="option" aria-selected={false} onClick={() => setTo(o)}>
            <span aria-hidden>{o.kind === "room" ? "#" : "@"}</span> {o.name}</button></li>))}</ul>}
        <div className="vx-share-obj" data-share-object=""><RefChip text={chip} /></div>
        {line && <p className="vx-share-line" data-share-line="">{line}</p>}
      </div>
    </Dialog>
  );
}

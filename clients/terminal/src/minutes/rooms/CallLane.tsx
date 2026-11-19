"use client";
/** THE ROOM CALL'S LANE (R1 2.3, `rooms`; ADR "Chats can be calls; Vexa is on the call by default and
 *  updates workspace files", founder 2026-10-15). Under the call bar, beside Vexa's tile:
 *
 *  - one line saying how Vexa writes on this call: members only, it updates the workspace as people
 *    talk; with a guest on the call, its changes become proposals a member accepts (a guest's speech
 *    never changes the workspace by itself);
 *  - what Vexa proposed or updated since the call started, newest first: Accept / Decline on a
 *    proposal this person may decide, Undo on a change Vexa made (members only);
 *  - Transcript: the call's transcript, notes and shown page, in the right panel.
 *
 *  The recap needs nothing here: a room call's recap is posted to the room. */
import { useState } from "react";
import { FileText, UserRound, Users } from "lucide-react";
import { decide, undoChange, type LaneItem, type OnCall } from "./laneData";
import "./rooms.css";

const SHOWN = 4;

export function CallLane({ room, guests, items, onTranscript, onDecided }: {
  /** the call's room id — what its change list and undo are addressed by */
  room: string;
  guests: OnCall[];
  items: LaneItem[];
  onTranscript?: () => void;
  onDecided?: () => void;
}) {
  const [err, setErr] = useState<{ id: string; line: string } | null>(null);
  const [busy, setBusy] = useState("");
  const act = async (id: string, a: "accept" | "decline") => {
    setBusy(id); setErr(null);
    const line = await decide(id, a);
    setBusy("");
    if (line) setErr({ id, line }); else onDecided?.();
  };
  const undo = async (id: string) => {
    setBusy(id); setErr(null);
    const line = await undoChange(room, id);
    setBusy("");
    if (line) setErr({ id, line }); else onDecided?.();
  };
  const names = guests.map((g) => g.name).join(", ");
  return (
    <section className="vx-ck-lane" aria-label="Vexa on this call" data-call-lane={guests.length ? "guest" : "members"}>
      <p className="vx-ck-lane-mode">
        {guests.length
          ? <><UserRound size={13} aria-hidden /><span><b>{names}</b> {guests.length === 1 ? "is a guest" : "are guests"}: Vexa proposes changes for members to accept.</span></>
          : <><Users size={13} aria-hidden /><span>Members only: Vexa updates the workspace as you talk.</span></>}
        {onTranscript && <button type="button" className="vx-ck-lane-link" onClick={onTranscript}><FileText size={12} aria-hidden /> Transcript</button>}
      </p>
      {items.length > 0 && <ul className="vx-ck-lane-list">
        {items.slice(0, SHOWN).map((it) => (
          <li key={it.id} data-lane-item={it.kind}>
            <span className="vx-ck-lane-what">
              <span>{it.kind === "proposed" ? "Proposes a change to " : it.undone ? "Undone: " : "Updated "}<b>{it.label}</b></span>
              {it.reason && <small>because {it.reason.replace(/^because\s+/i, "")}</small>}
            </span>
            {it.kind === "updated" && it.undoable && !it.undone && <span className="vx-ck-lane-acts">
              <button type="button" className="vx-btn" data-variant="ghost" disabled={busy === it.id} onClick={() => void undo(it.id)}>Undo</button>
            </span>}
            {it.kind === "proposed" && it.acts.length > 0 && <span className="vx-ck-lane-acts">
              {it.acts.includes("accept") && <button type="button" className="vx-btn" data-variant="primary" disabled={busy === it.id} onClick={() => void act(it.id, "accept")}>Accept</button>}
              {it.acts.includes("decline") && <button type="button" className="vx-btn" data-variant="ghost" disabled={busy === it.id} onClick={() => void act(it.id, "decline")}>Decline</button>}
            </span>}
            {err?.id === it.id && <p className="vx-rooms-error vx-ck-lane-err" role="alert">{err.line}</p>}
          </li>))}
      </ul>}
      {items.length > SHOWN && <p className="vx-rooms-note">and {items.length - SHOWN} more in the room</p>}
    </section>
  );
}

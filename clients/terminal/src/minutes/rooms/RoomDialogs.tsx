"use client";
/** Making and finding rooms (R1, `rooms`). A room is made IN a workspace and is OPEN by default
 *  (founder 2026-10-15: "room is visible to anyone after it's created, if it's public (default),
 *  chats are private"). "Take to a room…" is the same dialog, named after the chat it comes from:
 *  chats themselves are never shared. */
import { useEffect, useState } from "react";
import { Hash, Lock } from "lucide-react";
import { Modal } from "../../ui-kit/Modal";
import { createRoom, joinRoom, listRooms, type RoomListing, type Visibility } from "../../surfaces/roomsApi";
import type { WritableWorkspace } from "../../data/workspaceApi";
import "./rooms.css";

const refusal = (e: unknown): string => {
  const d = (e as { detail?: unknown; body?: { detail?: { refused?: string } } })?.body?.detail?.refused
    ?? ((e as { message?: string })?.message ?? "");
  if (/not_a_workspace_member/.test(String(d))) return "You are not a member of that workspace.";
  if (/feature_disabled/.test(String(d))) return "Rooms are not switched on here.";
  return "Could not make the room. Try again.";
};

/** New room — or, with `from`, "Take to a room…" (named after the chat; the chat stays private). */
export function NewRoomDialog({ workspaces, initialWorkspace, initialName = "", from, onClose, onCreated }: {
  workspaces: WritableWorkspace[]; initialWorkspace?: string; initialName?: string;
  /** the private chat this room is made from (Take to a room…) */
  from?: { label: string };
  onClose: () => void;
  onCreated: (session: string, name: string, workspace: string) => void;
}) {
  const shared = workspaces;
  const [ws, setWs] = useState(initialWorkspace && shared.some((w) => w.slug === initialWorkspace) ? initialWorkspace : shared[0]?.slug ?? "");
  const [name, setName] = useState(initialName);
  const [vis, setVis] = useState<Visibility>("open");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const wsName = shared.find((w) => w.slug === ws)?.name ?? ws;
  const create = async () => {
    if (!ws || !name.trim()) return;
    setBusy(true); setErr("");
    try {
      const r = await createRoom({ workspace: ws, name: name.trim(), visibility: vis });
      onCreated(r.session_id, name.trim(), ws);
    } catch (e) { setErr(refusal(e)); setBusy(false); }
  };
  return (
    <Modal title={from ? "Take to a room" : "New room"} onClose={onClose} width={420}>
      <form className="vx-rooms-form" data-new-room="" onSubmit={(e) => { e.preventDefault(); void create(); }}>
        {from && <p className="vx-rooms-note">A room for what you were thinking about in “{from.label}”. Your chat stays private; nothing from it is copied unless you paste it.</p>}
        {shared.length === 0
          ? <p className="vx-rooms-note">Rooms live in a shared workspace. Share one first, then make rooms in it.</p>
          : <>
            <label className="vx-rooms-field"><span>Workspace</span>
              <select className="vx-rooms-select" value={ws} onChange={(e) => setWs(e.target.value)}>
                {shared.map((w) => <option key={w.slug} value={w.slug}>{w.name}</option>)}</select></label>
            <label className="vx-rooms-field"><span>Name</span>
              <input autoFocus className="vx-rooms-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Launch plan" maxLength={120} /></label>
            <div className="vx-rooms-field"><span>Who can see it</span>
              <div className="vx-rooms-seg" role="radiogroup" aria-label="Who can see it">
                <button type="button" role="radio" aria-checked={vis === "open"} onClick={() => setVis("open")}><Hash size={11} aria-hidden /> Open</button>
                <button type="button" role="radio" aria-checked={vis === "closed"} onClick={() => setVis("closed")}><Lock size={11} aria-hidden /> Closed</button></div>
              <p className="vx-rooms-note">{vis === "open" ? `Everyone in ${wsName} can see and join it.` : "Only the people you add."}</p></div>
          </>}
        {err && <p className="vx-rooms-error" role="alert">{err}</p>}
        <div className="vx-rooms-acts">
          <button type="button" className="vx-btn" data-variant="secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="vx-btn" data-variant="primary" disabled={busy || !ws || !name.trim()}>Create room</button>
        </div>
      </form>
    </Modal>
  );
}

/** Browse open rooms of a workspace and join one. */
export function BrowseRoomsDialog({ workspaces, initialWorkspace, onClose, onOpen }: {
  workspaces: WritableWorkspace[]; initialWorkspace?: string; onClose: () => void; onOpen: (session: string) => void;
}) {
  const [ws, setWs] = useState(initialWorkspace ?? workspaces[0]?.slug ?? "");
  const [rooms, setRooms] = useState<RoomListing[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!ws) { setRooms([]); return; }
    let live = true; setRooms(null); setErr("");
    void listRooms(ws).then((r) => { if (live) setRooms(r); }).catch(() => { if (live) { setRooms([]); setErr("Could not read the rooms."); } });
    return () => { live = false; };
  }, [ws]);
  const open = async (r: RoomListing) => {
    try { if (!r.member) await joinRoom(r.id); onOpen(r.id); } catch { setErr("Could not join that room."); }
  };
  return (
    <Modal title="Open rooms" onClose={onClose} width={440}>
      <div className="vx-rooms-form" data-browse-rooms="">
        <label className="vx-rooms-field"><span>Workspace</span>
          <select className="vx-rooms-select" value={ws} onChange={(e) => setWs(e.target.value)}>
            {workspaces.map((w) => <option key={w.slug} value={w.slug}>{w.name}</option>)}</select></label>
        {rooms === null ? <p className="vx-rooms-note">…</p>
          : rooms.length === 0 ? <p className="vx-rooms-note">No rooms here yet.</p>
          : <ul className="vx-rooms-list">{rooms.map((r) => <li key={r.id}>
              {r.visibility === "closed" ? <Lock size={13} aria-hidden /> : <Hash size={13} aria-hidden />}
              <span><b>{r.title || "Room"}</b><small>{r.member ? "You are in it" : "Open to the workspace"}</small></span>
              <button type="button" className="vx-btn" data-variant={r.member ? "secondary" : "primary"} onClick={() => void open(r)}>{r.member ? "Open" : "Join"}</button></li>)}</ul>}
        {err && <p className="vx-rooms-error" role="alert">{err}</p>}
      </div>
    </Modal>
  );
}

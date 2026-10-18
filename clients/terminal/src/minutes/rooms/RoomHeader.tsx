"use client";
/** THE ROOM HEADER (R1, `rooms`; the approved mock, founder 2026-10-15: "this is mess" → essentials).
 *
 *  Left: the workspace's mark, the room's name with # (open) or a lock (closed), one quiet line —
 *  "Product planning · 3 members · Vexa: quiet". Right: who is in it, Call (Join when live) and "…":
 *  Vexa's setting (off · quiet · active), Schedule a call, Context (what Vexa sees), Think privately, Activity, Room
 *  settings (name, open/closed, the room's model, members) and Leave. No "Writes to" chip: a room always writes to its
 *  workspace. No second identity, no page buttons. 56 px. */
import { useEffect, useState, type ReactNode } from "react";
import { Hash, Lock, MoreHorizontal } from "lucide-react";
import { Menu, type MenuItem } from "../../ui-kit";
import { Modal } from "../../ui-kit/Modal";
import { header } from "../tokens";
import { Participants } from "../Participants";
import { CallButton } from "../CallButton";
import { ChatName } from "../ChatName";
import { openRoomToWorkspace, setRoomLevel, setRoomProactivity, setRoomVisibility, type Proactivity, type RoomSettings } from "../../surfaces/roomsApi";
import { getModelCatalog, type ModelEntry } from "../../surfaces/modelsApi";
import { refreshRoom, rememberRoom } from "../../surfaces/roomState";
import { WorkspaceMark } from "./WorkspaceMark";
import { ScheduleCallDialog } from "./ScheduleCallDialog";
import "./rooms.css";

export const MODE_WORD: Record<Proactivity, string> = { off: "answers when asked", quiet: "speaks up when it matters", active: "joins in" };

export function RoomHeader({ session, label, room, workspaceName: givenName, leading, onActivity, onRename, onLeave, onThinkPrivately, onOpenWorkspace }: {
  session: string; label: string; room: RoomSettings; workspaceName: string; leading?: ReactNode;
  /** "…" → Activity: the routines working in this room (the shell shows them; not as a strip) */
  onActivity?: () => void;
  onRename?: (name: string) => Promise<void>;
  onLeave: () => void;
  onThinkPrivately: () => void;
  onOpenWorkspace?: () => void;
}) {
  const [modal, setModal] = useState<"" | "context" | "settings" | "schedule">("");
  const [err, setErr] = useState("");
  const closed = room.visibility === "closed";
  // while the room is in front, its state is read again every few seconds: what Vexa is doing here
  // ("checking…") can start without anyone in this tab asking
  useEffect(() => { const t = window.setInterval(() => { if (!document.hidden) void refreshRoom(session); }, 5000); return () => window.clearInterval(t); }, [session]);
  // a workspace the person cannot write to is not in the shell's list, which then hands the SLUG:
  // resolve its name rather than show "finos-a396af" (JTBD-1)
  const [resolved, setResolved] = useState<string | null>(null);
  useEffect(() => {
    if (givenName && givenName !== room.workspace) return;
    let on = true;
    void import("../../ui-kit/wsLinks").then((m) => m.workspaceBySlug(room.workspace))
      .then((rec) => { if (on && rec?.name) setResolved(rec.name); }).catch(() => undefined);
    return () => { on = false; };
  }, [givenName, room.workspace]);
  const workspaceName = resolved ?? (givenName === room.workspace ? "" : givenName);
  const setMode = (p: Proactivity) => {
    setErr("");
    void setRoomProactivity(session, p).then((r) => rememberRoom(session, r)).catch(() => setErr("Could not change Vexa's setting. Try again."));
  };
  const items: MenuItem[] = [
    ...(["off", "quiet", "active"] as const).map((m, i) => ({ key: `vexa-${m}`, label: `Vexa ${MODE_WORD[m]}`, checked: room.proactivity === m, separatorBefore: i === 0 ? false : undefined, onSelect: () => setMode(m), data: { "data-room-act": `vexa-${m}` } })),
    { key: "schedule", label: "Schedule a call…", separatorBefore: true, onSelect: () => setModal("schedule"), data: { "data-room-act": "schedule" } },
    { key: "context", label: "Context — what Vexa sees…", onSelect: () => setModal("context"), data: { "data-room-act": "context" } },
    { key: "think", label: "Think privately", onSelect: onThinkPrivately, data: { "data-room-act": "think" } },
    ...(onActivity ? [{ key: "activity", label: "Activity…", onSelect: onActivity, data: { "data-room-act": "activity" } }] : []),
    { key: "settings", label: "Room settings…", onSelect: () => setModal("settings"), data: { "data-room-act": "settings" } },
    { key: "leave", label: "Leave room", danger: true, separatorBefore: true, onSelect: onLeave, data: { "data-room-act": "leave" } },
  ];
  return (
    <div className="vx-pane vx-rh" data-pane="header" data-room-header="" style={header}>
      {leading}
      <WorkspaceMark id={room.workspace} name={workspaceName || room.workspace || label} size={30} />
      <div className="vx-rh-id">
        <div className="vx-rh-name">{closed ? <Lock size={13} aria-label="Closed room" /> : <Hash size={13} aria-label="Open room" />}
          <ChatName key={session} label={label} onRename={onRename} /></div>
        <div className="vx-rh-sub">
          {workspaceName && (onOpenWorkspace
            ? <button type="button" className="vx-rh-ws" title={`Open ${workspaceName}`} onClick={onOpenWorkspace}>{workspaceName}</button>
            : <span>{workspaceName}</span>)}
          {workspaceName ? " · " : ""}{room.humans} {room.humans === 1 ? "member" : "members"} · {room.agent_busy
            ? <span className="vx-rh-busy" role="status">Vexa is checking…</span> : <>Vexa {MODE_WORD[room.proactivity]}</>}
          {err && <span role="alert"> · {err}</span>}
        </div>
      </div>
      <span className="vx-rh-gap" />
      <Participants key={`people:${session}`} session={session} />
      <span className="vx-rh-call"><CallButton key={`call:${session}`} session={session} label={label} /></span>
      <Menu label="More for this room" variant="ghost" align="end" items={items} triggerData={{ "data-room-more": "" }}
        trigger={<MoreHorizontal size={16} strokeWidth={1.75} aria-hidden />} />
      {modal === "context" && <Modal title="What Vexa sees in this room" onClose={() => setModal("")} width={420}>
        <div className="vx-rooms-form">
          <p>{closed ? "A closed room: only its members see it" : "An open room: every member of the workspace can see and join it"}{workspaceName ? <> — in <b>{workspaceName}</b></> : ""}.</p>
          <p>Vexa reads this room's conversation and the <b>{workspaceName || "workspace"}</b> workspace. It never reads anyone's private chats.</p>
        </div></Modal>}
      {modal === "schedule" && <ScheduleCallDialog session={session} label={label} workspace={room.workspace} workspaceName={workspaceName} onClose={() => setModal("")} />}
      {modal === "settings" && <RoomSettingsDialog session={session} label={label} room={room} workspaceName={workspaceName} onRename={onRename} onClose={() => setModal("")} />}
    </div>
  );
}

export function RoomSettingsDialog({ session, label, room, workspaceName, onRename, onClose }: {
  session: string; label: string; room: RoomSettings; workspaceName: string; onRename?: (name: string) => Promise<void>; onClose: () => void;
}) {
  const [name, setName] = useState(label);
  const [closed, setClosed] = useState(room.visibility === "closed");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [level, setLevel] = useState(room.level ?? "");
  const [models, setModels] = useState<ModelEntry[] | null>(null);
  useEffect(() => { let live = true; getModelCatalog().then((l) => { if (live) setModels(l.models ?? []); }).catch(() => { if (live) setModels([]); }); return () => { live = false; }; }, []);
  const opening = room.visibility === "closed" && !closed;
  const save = async () => {
    setErr(""); setBusy(true);
    try {
      if (name.trim() && name.trim() !== label) await onRename?.(name.trim());
      if (level !== (room.level ?? "")) rememberRoom(session, await setRoomLevel(session, level));
      if (opening) {
        // CLOSED → OPEN shows the history to every member of the workspace: said, then confirmed
        if (!confirming) { setConfirming(true); setBusy(false); return; }
        rememberRoom(session, await openRoomToWorkspace(session, { workspace: room.workspace, confirmed: true }));
      } else if (closed && room.visibility !== "closed") rememberRoom(session, await setRoomVisibility(session, "closed"));
      onClose();
    } catch { setErr("Could not save. You may not be allowed to change this room."); }
    setBusy(false);
  };
  return (
    <Modal title="Room settings" onClose={onClose} width={420}>
      <div className="vx-rooms-form" data-room-settings="">
        <label className="vx-rooms-field"><span>Name</span><input className="vx-rooms-input" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <div className="vx-rooms-field"><span>Who can see it</span>
          <div className="vx-rooms-seg" role="radiogroup" aria-label="Who can see it">
            <button type="button" role="radio" aria-checked={!closed} onClick={() => { setClosed(false); setConfirming(false); }}><Hash size={11} aria-hidden /> Open</button>
            <button type="button" role="radio" aria-checked={closed} onClick={() => { setClosed(true); setConfirming(false); }}><Lock size={11} aria-hidden /> Closed</button></div>
          <p className="vx-rooms-note">{closed ? "Only its members see it." : `Everyone in ${workspaceName || "the workspace"} can see and join it.`}</p></div>
        <label className="vx-rooms-field"><span>Model</span>
          <select className="vx-rooms-select" value={level} onChange={(e) => setLevel(e.target.value)} disabled={models === null}>
            <option value="">The default model</option>
            {level && models && !models.some((m) => m.id === level) && <option value={level}>{level}</option>}
            {(models ?? []).map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
          </select>
          <p className="vx-rooms-note">One model for everybody in this room, from its next turn.</p></label>
        {confirming && <p className="vx-rooms-note" role="alert"><b>Everyone in {workspaceName || "the workspace"} will see this room and its history.</b> Save again to open it.</p>}
        <div className="vx-rooms-field"><span>Members</span><Participants session={session} /></div>
        {err && <p className="vx-rooms-error" role="alert">{err}</p>}
        <div className="vx-rooms-acts">
          <button type="button" className="vx-btn" data-variant="secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="vx-btn" data-variant="primary" disabled={busy} onClick={() => void save()}>{confirming ? "Open to the workspace" : "Save"}</button>
        </div>
      </div>
    </Modal>
  );
}

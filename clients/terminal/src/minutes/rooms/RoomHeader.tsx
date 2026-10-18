"use client";
/** THE ROOM HEADER (R1, `rooms`; UI 7z, founder 2026-10-17; 0.13.4 S4).
 *
 *  # name · workspace chip · people pill · Start call · Invite · ⋯ — and nothing else. The ⋯ holds the
 *  two switches ("Vexa in this room", "Transcribe calls", 7hh), Vexa's setting (off · quiet · active),
 *  Schedule a call, Context, Think privately, Activity, Room settings and Leave. A quiet indicator sits
 *  by the name when Vexa or transcription is off. No "Writes to" chip: a room always writes to its
 *  workspace. The people pill opens the members panel; Invite opens it on its picker.
 *
 *  A ROOM OF TWO (FD4) has its own header: the other person's face and name, Start call, ⋯ (the
 *  switches, Schedule a call, Think privately). No workspace chip — its pair workspace is hidden.
 *
 *  ON A PHONE (ui-design §3; PRD §11.3 fix 6): below 520px of header width the header folds to
 *  name · people · ⋯, and Start call, Invite and both switches sit inside ⋯. 56 px at most. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Hash, Lock, MoreHorizontal, Phone, UserPlus } from "lucide-react";
import { Identity, IdentityStack, Menu, type MenuItem } from "../../ui-kit";
import { CALL_START_EVENT } from "../../platform/events";
import { readRoomPeople, type RoomPeople } from "../../surfaces/roomPeopleApi";
import { MembersPanel } from "./MembersPanel";
import { SwitchAskCards, SwitchIndicators, switchItems, useRoomSwitches } from "./roomSwitches";
import { objectActions } from "../readme/ObjectActions";
import { ShareDialog } from "../readme/ShareDialog";
import { PinnedStrip } from "../readme/PinnedStrip";
import { Modal } from "../../ui-kit/Modal";
import { header } from "../tokens";
import { CallButton } from "../CallButton";
import { ChatName } from "../ChatName";
import { openRoomToWorkspace, setRoomLevel, setRoomProactivity, setRoomVisibility, type Proactivity, type RoomSettings } from "../../surfaces/roomsApi";
import { getModelCatalog, type ModelEntry } from "../../surfaces/modelsApi";
import { refreshRoom, rememberRoom } from "../../surfaces/roomState";
import { WorkspaceMark } from "./WorkspaceMark";
import { ScheduleCallDialog } from "./ScheduleCallDialog";
import "./rooms.css";
import "./roomPeople.css";

export const MODE_WORD: Record<Proactivity, string> = { off: "answers when asked", quiet: "speaks up when it matters", active: "joins in" };

/** The header's width, for the fold: below 520px the header is name · people · ⋯ (a container
 *  query on the pane, not the window, ui-design §3). */
function useNarrow(ref: React.RefObject<HTMLElement | null>, at = 520): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((es) => { for (const e of es) setNarrow(e.contentRect.width < at); });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, at]);
  return narrow;
}

/** Who is in the room, for the pill and the room of two's face. Re-read when the panel closes. */
function useRoomPeople(session: string, version: number): RoomPeople | null {
  const [p, setP] = useState<RoomPeople | null>(null);
  useEffect(() => {
    let live = true;
    readRoomPeople(session).then((d) => { if (live) setP(d); }).catch(() => { if (live) setP(null); });
    return () => { live = false; };
  }, [session, version]);
  return p;
}

export function RoomHeader({ session, label, room, workspaceName: givenName, leading, onActivity, onRename, onLeave, onThinkPrivately, onOpenWorkspace }: {
  session: string; label: string; room: RoomSettings; workspaceName: string; leading?: ReactNode;
  /** "…" → Activity: the routines working in this room (the shell shows them; not as a strip) */
  onActivity?: () => void;
  onRename?: (name: string) => Promise<void>;
  onLeave: () => void;
  onThinkPrivately: () => void;
  onOpenWorkspace?: () => void;
}) {
  const [modal, setModal] = useState<"" | "context" | "settings" | "schedule" | "share">("");
  const [panel, setPanel] = useState<"" | "members" | "invite">("");
  const [peopleV, setPeopleV] = useState(0);
  const [err, setErr] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const narrow = useNarrow(box);
  const people = useRoomPeople(session, peopleV);
  const { sw, err: swErr, flip, answer } = useRoomSwitches(session);
  const closed = room.visibility === "closed";
  const pair = !!(sw?.pair || people?.pair);
  // while the room is in front, its state is read again every few seconds: what Vexa is doing here
  // ("checking…") can start without anyone in this tab asking
  useEffect(() => { const t = window.setInterval(() => { if (!document.hidden) void refreshRoom(session); }, 5000); return () => window.clearInterval(t); }, [session]);
  // a workspace the person cannot write to is not in the shell's list, which then hands the SLUG:
  // resolve its name rather than show "finos-a396af" (JTBD-1)
  const [resolved, setResolved] = useState<string | null>(null);
  useEffect(() => {
    if (pair || (givenName && givenName !== room.workspace)) return;
    let on = true;
    void import("../../ui-kit/wsLinks").then((m) => m.workspaceBySlug(room.workspace))
      .then((rec) => { if (on && rec?.name) setResolved(rec.name); }).catch(() => undefined);
    return () => { on = false; };
  }, [givenName, room.workspace, pair]);
  const workspaceName = resolved ?? (givenName === room.workspace ? "" : givenName);
  const setMode = (p: Proactivity) => {
    setErr("");
    void setRoomProactivity(session, p).then((r) => rememberRoom(session, r)).catch(() => setErr("Could not change Vexa's setting. Try again."));
  };
  const startCall = () => window.dispatchEvent(new CustomEvent(CALL_START_EVENT, { detail: { session } }));
  const humans = (people?.members ?? []).filter((x) => x.kind === "human");
  const other = pair ? humans.find((x) => x.principal !== people?.you) : undefined;
  const folded: MenuItem[] = narrow ? [
    { key: "call", label: "Start call", icon: <Phone size={14} aria-hidden />, onSelect: startCall, data: { "data-room-act": "call" } },
    ...(pair ? [] : [{ key: "invite", label: "Invite…", icon: <UserPlus size={14} aria-hidden />, onSelect: () => setPanel("invite"), data: { "data-room-act": "invite" } }]),
  ] : [];
  const items: MenuItem[] = [
    ...folded,
    ...switchItems(sw, (n, v) => void flip(n, v)).map((it, i) => ({ ...it, separatorBefore: i === 0 && folded.length > 0 })),
    ...(["off", "quiet", "active"] as const).map((m, i) => ({ key: `vexa-${m}`, label: `Vexa ${MODE_WORD[m]}`, checked: room.proactivity === m, separatorBefore: i === 0, disabled: sw ? !sw.vexa : false, onSelect: () => setMode(m), data: { "data-room-act": `vexa-${m}` } })),
    { key: "schedule", label: "Schedule a call…", separatorBefore: true, onSelect: () => setModal("schedule"), data: { "data-room-act": "schedule" } },
    ...(pair ? [] : [{ key: "context", label: "Context — what Vexa sees…", onSelect: () => setModal("context"), data: { "data-room-act": "context" } }]),
    { key: "think", label: "Think privately", onSelect: onThinkPrivately, data: { "data-room-act": "think" } },
    ...(onActivity ? [{ key: "activity", label: "Activity…", onSelect: onActivity, data: { "data-room-act": "activity" } }] : []),
    ...(pair ? [] : [{ key: "settings", label: "Room settings…", onSelect: () => setModal("settings"), data: { "data-room-act": "settings" } },
      // S7 (7gg, 7ff): a room is a single object — Copy reference, Share (send it to a room or a person)
      ...objectActions({ kind: "room", id: session, label }, { onShare: () => setModal("share") }).map((it, i) => ({ ...it, separatorBefore: i === 0 ? true : undefined })),
      { key: "leave", label: "Leave room", danger: true, separatorBefore: true, onSelect: onLeave, data: { "data-room-act": "leave" } }]),
  ];
  const pill = (
    <button type="button" className="vx-rh-people" data-members={humans.length || room.humans}
      aria-label={`${humans.length || room.humans} ${(humans.length || room.humans) === 1 ? "member" : "members"} — show`}
      onClick={() => setPanel(panel === "members" ? "" : "members")}>
      <IdentityStack size={22} max={3} items={humans.map((x) => ({ kind: "person" as const, id: x.principal, name: x.name }))} />
      <span>{humans.length || room.humans}</span>
    </button>
  );
  return (
    <div ref={box} className="vx-pane vx-rh" data-pane="header" data-room-header="" data-room-of-two={pair ? "" : undefined}
      data-narrow={narrow ? "" : undefined} style={header}>
      {leading}
      {pair && other ? <>
        <Identity kind="person" id={other.principal} name={other.name} size={28} />
        <div className="vx-rh-id">
          <div className="vx-rh-name"><span>{other.name}</span></div>
          <div className="vx-rh-sub">A room of two{room.agent_busy ? <> · <span className="vx-rh-busy" role="status">Vexa is checking…</span></> : ""}</div>
        </div>
      </> : <>
        {!narrow && <WorkspaceMark id={room.workspace} name={workspaceName || room.workspace || label} size={30} />}
        <div className="vx-rh-id">
          <div className="vx-rh-name">{closed ? <Lock size={13} aria-label="Closed room" /> : <Hash size={13} aria-label="Open room" />}
            <ChatName key={session} label={label} onRename={onRename} /></div>
          <div className="vx-rh-sub">
            {workspaceName && !narrow && (onOpenWorkspace
              ? <button type="button" className="vx-rh-ws" data-ws-chip="" title={`Open ${workspaceName}`} onClick={onOpenWorkspace}>{workspaceName}</button>
              : <span data-ws-chip="">{workspaceName}</span>)}
            {workspaceName && !narrow ? " · " : ""}{room.agent_busy
              ? <span className="vx-rh-busy" role="status">Vexa is checking…</span> : <>Vexa {MODE_WORD[room.proactivity]}</>}
            {err && <span role="alert"> · {err}</span>}
          </div>
          {/* S7 (7ee): the room's shared pins, resolved for this viewer; absent when there are none */}
          <PinnedStrip workspace={room.workspace} room={session} canPin />
        </div>
      </>}
      <SwitchIndicators sw={sw} />
      <span className="vx-rh-gap" />
      {!pair && pill}
      {!narrow && <span className="vx-rh-call"><CallButton key={`call:${session}`} session={session} label={label} /></span>}
      {!narrow && !pair && <button type="button" className="vx-btn" data-variant="secondary" data-invite="" onClick={() => setPanel("invite")}>
        <UserPlus size={13} aria-hidden /> Invite</button>}
      <Menu label={pair ? "More for this conversation" : "More for this room"} variant="ghost" align="end" items={items} triggerData={{ "data-room-more": "" }}
        trigger={<MoreHorizontal size={16} strokeWidth={1.75} aria-hidden />} />
      {(swErr || (sw && Object.keys(sw.asks ?? {}).length > 0)) && <div className="vx-rh-below"><SwitchAskCards sw={sw} answer={(n, a) => void answer(n, a)} err={swErr} /></div>}
      <MembersPanel session={session} label={label} open={!!panel} startWith={panel === "invite" ? "invite" : "members"}
        onClose={() => { setPanel(""); setPeopleV((v) => v + 1); }} />
      {modal === "context" && <Modal title="What Vexa sees in this room" onClose={() => setModal("")} width={420}>
        <div className="vx-rooms-form">
          <p>{closed ? "A closed room: only its members see it" : "An open room: every member of the workspace can see and join it"}{workspaceName ? <> — in <b>{workspaceName}</b></> : ""}.</p>
          <p>Vexa reads this room's conversation and the <b>{workspaceName || "workspace"}</b> workspace. It never reads anyone's private chats.</p>
          {sw && !sw.vexa && <p><b>Vexa is switched off here:</b> it does not read or answer this room until it is switched back on.</p>}
        </div></Modal>}
      {modal === "schedule" && <ScheduleCallDialog session={session} label={label} workspace={room.workspace} workspaceName={workspaceName} onClose={() => setModal("")} />}
      {modal === "share" && <ShareDialog object={{ kind: "room", id: session, label }} onClose={() => setModal("")} />}
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
        <p className="vx-rooms-note">Who is in the room is in the members panel: the people beside its name.</p>
        {err && <p className="vx-rooms-error" role="alert">{err}</p>}
        <div className="vx-rooms-acts">
          <button type="button" className="vx-btn" data-variant="secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="vx-btn" data-variant="primary" disabled={busy} onClick={() => void save()}>{confirming ? "Open to the workspace" : "Save"}</button>
        </div>
      </div>
    </Modal>
  );
}

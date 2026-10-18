"use client";
/** THE CALL DOCK, presentational (UI decisions 7–10a, 14): one compact bar at the top of the
 *  conversation column — the call's name, the time, who is in it (a ring on whoever speaks), Vexa as a
 *  small listening mark, the profile, a quiet "transcribed" mark — and the controls as icon buttons:
 *  mic, camera, screen share, "…" (devices, a private chat, adding people), expand, and Leave as a
 *  quiet red icon at the end. When anyone's camera or a shared screen is on, the tiles open under the
 *  bar; otherwise the bar stays one line. No transcript here: it lives in the meeting sidebar.
 *  Props in, callbacks out — the LiveKit room (CallRoom) and the UI lab both feed it. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AudioLines, Maximize2, Mic, MicOff, MonitorUp, MoreHorizontal, PhoneOff, PhoneOutgoing, Video, VideoOff } from "lucide-react";
import { Button } from "../../ui-kit/primitives/Button";
import type { CallPerson, ScreenShare } from "./types";
import { clock } from "./types";
import { Roster, VexaMark } from "./People";
import { CallStage } from "./Stage";
import "./callkit.css";

export interface CallMenuItem { id: string; label: string; onSelect: () => void; hint?: string }

export interface CallBarProps {
  /** the call's one name (`meetingDisplayName`) */
  name: string;
  /** the name is a way back to the call's own chat */
  onName?: () => void;
  /** seconds since the call started */
  elapsed: number;
  /** the people in the call, you included; Vexa is never one of them */
  people: CallPerson[];
  /** the participant whose voice the room hears now */
  activeId?: string;
  /** Vexa is in the call, listening (the transcription is on) */
  transcribed: boolean;
  /** the sound of a shared screen is transcribed too (the room's notice says so) */
  screenAudio?: boolean;
  /** Vexa is speaking (it answers out loud) */
  vexaSpeaking?: boolean;
  /** the call's profile, shown as a chip */
  profile?: string;
  mic: boolean;
  cam: boolean;
  sharing: boolean;
  /** a listener joins without a microphone, camera or screen */
  canPublish?: boolean;
  onMic: () => void;
  onCam: () => void;
  onShare: () => void;
  onLeave: () => void;
  onExpand?: () => void;
  /** the "…" menu */
  menu?: CallMenuItem[];
  /** shown inside the "…" menu (the device pickers) */
  devices?: ReactNode;
  share?: ScreenShare | null;
  narrow?: boolean;
  /** a control on your own video tile (background blur) */
  selfAction?: ReactNode;
}

function Icon({ label, onClick, pressed, tone, children, data }: {
  label: string; onClick: () => void; pressed?: boolean; tone?: "off" | "leave"; children: ReactNode; data?: string;
}) {
  return (
    <button type="button" className="vx-cb-btn" aria-label={label} title={label} aria-pressed={pressed} data-tone={tone} data-ctl={data}
      onClick={onClick}>{children}</button>
  );
}

export function CallBar(p: CallBarProps) {
  const [menu, setMenu] = useState(false);
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!menu) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setMenu(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    window.addEventListener("mousedown", away); window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", away); window.removeEventListener("keydown", esc); };
  }, [menu]);
  const publish = p.canPublish !== false;
  const video = !!p.share || p.people.some((x) => x.camera && x.feed);
  const speaking = p.people.find((x) => x.speaking);
  return (
    <div className="vx-cb" data-video={video ? "" : undefined} data-narrow={p.narrow ? "" : undefined}>
      <div className="vx-cb-row">
        <button type="button" className="vx-cb-name" onClick={p.onName} title={p.onName ? `${p.name} — back to the call's chat` : p.name}>{p.name}</button>
        <span className="vx-cb-time" aria-label={`In the call for ${clock(p.elapsed)}`}>{clock(p.elapsed)}</span>
        <Roster people={p.people} size={20} max={p.narrow ? 3 : 5} />
        {p.transcribed && (
          <span className="vx-cb-vexa" data-speaking={p.vexaSpeaking ? "" : undefined}
            title={p.vexaSpeaking ? "Vexa is speaking" : `Vexa is listening · transcribed${p.screenAudio ? ", a shared screen's sound too" : ""} — everyone in the call sees this`}
            aria-label={p.vexaSpeaking ? "Vexa is speaking" : `Vexa is listening; the call is transcribed${p.screenAudio ? ", including the sound of a shared screen," : ""} and everyone in it sees this`}>
            <VexaMark size={16} /><span className="vx-ck-listen" aria-hidden />
          </span>
        )}
        {!p.transcribed && <span className="vx-cb-quiet" title="This call is not transcribed"><AudioLines size={12} aria-hidden /> not transcribed</span>}
        {p.profile && !p.narrow && <span className="vx-cb-chip" title="The profile this call runs under">{p.profile}</span>}
        {speaking && !p.narrow && <span className="vx-cb-quiet vx-cb-speaking" aria-live="polite">{speaking.self ? "You" : speaking.name.split(" ")[0]} speaking…</span>}
        <span className="vx-cb-gap" />
        {publish && <Icon label={p.mic ? "Mute" : "Unmute"} tone={p.mic ? undefined : "off"} onClick={p.onMic} data="mic">{p.mic ? <Mic size={15} /> : <MicOff size={15} />}</Icon>}
        {publish && <Icon label={p.cam ? "Turn camera off" : "Turn camera on"} tone={p.cam ? undefined : "off"} onClick={p.onCam} data="cam">{p.cam ? <Video size={15} /> : <VideoOff size={15} />}</Icon>}
        {publish && !p.narrow && <Icon label={p.sharing ? "Stop sharing your screen" : "Share your screen"} pressed={p.sharing} onClick={p.onShare} data="share"><MonitorUp size={15} /></Icon>}
        {(p.menu?.length || p.devices) && (
          <span ref={box} className="vx-cb-menuwrap">
            <Icon label="More" pressed={menu} onClick={() => setMenu((v) => !v)} data="more"><MoreHorizontal size={15} /></Icon>
            {menu && (
              <div className="vx-cb-menu" role="menu" aria-label="Call options">
                {p.menu?.map((m) => (
                  <button key={m.id} type="button" role="menuitem" className="vx-cb-item" data-item={m.id} title={m.hint}
                    onClick={() => { setMenu(false); m.onSelect(); }}>{m.label}</button>
                ))}
                {p.devices && <div className="vx-cb-devices" aria-label="Devices">{p.devices}</div>}
              </div>
            )}
          </span>
        )}
        {p.onExpand && <Icon label="Open the call page" onClick={p.onExpand} data="expand"><Maximize2 size={14} /></Icon>}
        <Icon label="Leave the call" tone="leave" onClick={p.onLeave} data="leave"><PhoneOff size={15} /></Icon>
      </div>
      {video && <CallStage people={p.people} share={p.share} activeId={p.activeId} narrow={p.narrow} selfAction={p.selfAction} />}
    </div>
  );
}

/** Before joining: the same one line — the name, the notice that the call is transcribed (quiet,
 *  never a banner; everyone in the call sees it), the microphone choice, Join and Cancel. Nothing
 *  connects and no microphone opens until Join. */
export function CallJoinBar({ name, transcribed, listener, mic, onMic, onJoin, onCancel, extra }: {
  name: string; transcribed: boolean; listener?: boolean; mic: boolean;
  onMic: () => void; onJoin: () => void; onCancel: () => void;
  /** more choices before joining (background blur) */
  extra?: ReactNode;
}) {
  return (
    <div className="vx-cb" data-call="prejoin">
      <div className="vx-cb-row">
        <span className="vx-cb-name">{name}</span>
        <span className="vx-cb-quiet" data-call="transcribed" role="status">
          {transcribed ? <><VexaMark size={14} /> This call is transcribed · everyone in it sees this</> : "This call is not transcribed"}
        </span>
        {listener && <span className="vx-cb-quiet">You join to listen</span>}
        <span className="vx-cb-gap" />
        {!listener && extra}
        {!listener && <Icon label={mic ? "Join with the microphone on" : "Join muted"} tone={mic ? undefined : "off"} onClick={onMic} data="mic">{mic ? <Mic size={15} /> : <MicOff size={15} />}</Icon>}
        <button type="button" className="vx-cb-text" onClick={onCancel}>Cancel</button>
        {/* JOIN IS THE ONE PRIMARY ACTION, outlined with a green live dot — never a solid fill (UI 7r/7x) */}
        <Button variant="primary" size="sm" data-call="prejoin-join" icon={<span className="vx-cb-livedot" />} onClick={onJoin}>Join</Button>
      </div>
    </div>
  );
}

/** AN OUTGOING CALL (call-person-autojoin): the person asked the agent to call someone, is already in
 *  the call, and nobody else has joined yet. One quiet line under the bar — whom they are calling —
 *  and Cancel, which leaves the call (an emptied call ends on its own). Kit Button, no fill (UI 7r). */
export function CallOutgoing({ to, onCancel }: { to: string; onCancel: () => void }) {
  return (
    <div className="vx-cb-outgoing" data-call="outgoing">
      <span className="vx-cb-quiet" role="status" aria-live="polite">
        <PhoneOutgoing size={12} aria-hidden /> Calling {to}…
      </span>
      <span className="vx-cb-gap" />
      <Button variant="secondary" size="sm" data-call="outgoing-cancel" onClick={onCancel}>Cancel</Button>
    </div>
  );
}

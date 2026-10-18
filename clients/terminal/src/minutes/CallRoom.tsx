"use client";
/** The call itself — LiveKit's React components (Apache-2.0) over the deployment's own LiveKit
 *  server (ADR-0048 decision 6), drawn as the call dock's one compact bar (`callkit/CallBar`, UI
 *  decisions 7–10a, 14). Audio first: who is in the call with a ring on whoever speaks, Vexa as a
 *  small listening mark, mute; the camera and a shared screen are one icon away and their tiles open
 *  under the bar when anyone turns one on. Devices, the private meeting view and adding people sit
 *  in "…". Sharing a line to the room is the chat's own composer (a shared chat is one stream), so
 *  the dock has no text box.
 *
 *  Capture uses the browser's own echo cancellation, noise suppression and gain control, and Opus
 *  with DTX and redundancy (RED) on the way out. The notice that the call is transcribed comes from
 *  the room's own metadata, set by meeting-api when it created the room, so every participant's
 *  client shows the same thing. */
import "@livekit/components-styles";
import {
  LiveKitRoom, MediaDeviceSelect, RoomAudioRenderer, VideoTrack, isTrackReference, useLocalParticipant, useParticipants,
  useDataChannel, useRoomInfo, useSpeakingParticipants, useTracks,
} from "@livekit/components-react";
import { receiveShow, setShowTransport, SHOW_TOPIC, type ShowEvent } from "./callShow";
import { Track, type LocalVideoTrack } from "livekit-client";
import { applyBlur, BLUR_WORD, cycleBlur, nextBlur, useBlur } from "./callBlur";
import { BlurButton, BlurNotice } from "./BlurButton";
import { useEffect, useState } from "react";
import { copyCallInvite, hasCallInvite, stopCallInvite } from "./callInvite";
import { toast } from "../ui-kit/primitives/Toast";
import type { ActiveCall } from "./callState";
import { CallBar, CallJoinBar, type CallMenuItem } from "./callkit/CallBar";
import type { CallPerson } from "./callkit/types";
import "./calls.css";
import { useFeature } from "../platform/features";
import { useRoom } from "../surfaces/roomState";
import { AgentChip, VexaMark } from "./callkit/People";
import { CallLane } from "./rooms/CallLane";
import { guestsOn, useCallLane, useRoomMembers } from "./rooms/laneData";
import "./rooms/rooms.css";

export interface CallRoomProps {
  call: ActiveCall;
  /** the call's one name (`meetingDisplayName`) */
  name: string;
  /** the same, once the people in the call are known ("Call with Valeriia") */
  nameWith?: (people: string[]) => string;
  onLeave: () => void;
  /** the meeting's own view: its note, the transcript drawer, a private chat about it */
  onPrivate: () => void;
  /** back to the call's own chat */
  onName?: () => void;
  /** add people (the chat's @ — sharing the chat with them) */
  onAddPeople?: () => void;
  /** open the call's meeting in the right panel: its transcript, notes and shown page */
  onOpenMeeting?: () => void;
}

/** The listener that transcribes joins hidden; it is never one of the people. */
const LISTENER = /^vexa-(listener|meeting-api)/;

export function transcribedNotice(metadata: string | undefined, fallback: boolean): boolean {
  try {
    const m = JSON.parse(metadata || "{}");
    return typeof m?.transcribed === "boolean" ? m.transcribed : fallback;
  } catch { return fallback; }
}

export default function CallRoom(props: CallRoomProps) {
  const { call, name, onLeave } = props;
  const listener = call.join.role === "viewer";
  const [joined, setJoined] = useState(false);
  const [mic, setMic] = useState(true);
  if (!joined) {
    return <CallJoinBar name={name} transcribed={call.join.call.transcribed !== false} listener={listener} mic={mic}
      onMic={() => setMic((v) => !v)} onJoin={() => setJoined(true)} onCancel={onLeave} extra={<BlurButton />} />;
  }
  return (
    <LiveKitRoom serverUrl={call.join.url} token={call.join.token} connect
      audio={listener || !mic ? false : { echoCancellation: true, noiseSuppression: true, autoGainControl: true }}
      video={false}
      options={{ adaptiveStream: true, dynacast: true,
                 publishDefaults: { dtx: true, red: true, audioPreset: { maxBitrate: 32_000 } } }}
      onDisconnected={onLeave}
      // NO LiveKit theme: its dark background under our own text made the call's name dark-on-black
      // (founder 2026-10-15). The bar draws itself from the app's tokens, in both themes.
      className="vx-callroom" style={{ display: "flex", flexDirection: "column" }}>
      <RoomAudioRenderer />
      <Inside {...props} listener={listener} />
    </LiveKitRoom>
  );
}

function useElapsed(started: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  return started ? Math.max(0, now / 1000 - started) : 0;
}

function Inside({ call, name, nameWith, onLeave, onPrivate, onName, onAddPeople, onOpenMeeting, listener }: CallRoomProps & { listener: boolean }) {
  const info = useRoomInfo();
  // A CALL OPENS ITS MEETING IN THE RIGHT PANEL (founder 2026-10-15, UI decisions 10d): joining —
  // by Join or by a link — shows the transcript, the notes and any shown page beside the chat.
  const meetingId = call.join.call.meeting_id;
  useEffect(() => { if (meetingId && onOpenMeeting) onOpenMeeting(); }, [meetingId]); // eslint-disable-line react-hooks/exhaustive-deps
  const participants = useParticipants();
  const speaking = useSpeakingParticipants();
  const cams = useTracks([Track.Source.Camera, Track.Source.ScreenShare], { onlySubscribed: false }).filter(isTrackReference);
  const { localParticipant, isMicrophoneEnabled, isCameraEnabled, isScreenShareEnabled } = useLocalParticipant();
  const transcribed = transcribedNotice(info.metadata, call.join.call.transcribed);
  const roomsOn = useFeature("rooms");
  const roomCall = !!useRoom(call.session, roomsOn)?.room;
  const elapsed = useElapsed(call.join.call.started);
  // THE ROOM CALL'S LANE (2.3): who is a guest (not a room member) and what Vexa changed or proposed
  const members = useRoomMembers(call.session, roomCall);
  const lane = useCallLane(call.session, call.join.call.room, (call.join.call.started || 0) * 1000, roomCall);
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && window.innerWidth < 760);
  useEffect(() => { const f = () => setNarrow(window.innerWidth < 760); window.addEventListener("resize", f); return () => window.removeEventListener("resize", f); }, []);

  // A PAGE SHOWN TO EVERYONE travels on the room's data channel (topic vexa.show): the server sends
  // `show` and the agent's highlights; the presenter's browser sends where it scrolled and what it
  // selected. Nothing is stored.
  const { send } = useDataChannel(SHOW_TOPIC, (msg) => {
    try { receiveShow(JSON.parse(new TextDecoder().decode(msg.payload)) as ShowEvent); } catch { /* not ours */ }
  });
  useEffect(() => {
    const names = new Map(participants.map((p) => [p.identity, p.name || p.identity]));
    setShowTransport({ send: (e) => { void send(new TextEncoder().encode(JSON.stringify(e)), { reliable: true }); },
      me: localParticipant.identity, nameOf: (id) => names.get(id) ?? "someone" });
  }, [send, participants, localParticipant.identity]);
  useEffect(() => () => setShowTransport(null), []);
  // BACKGROUND BLUR, the person's setting, applied whenever the camera starts (and on every change)
  const blur = useBlur();
  const camTrack = (localParticipant.getTrackPublication?.(Track.Source.Camera)?.track ?? null) as LocalVideoTrack | null;
  useEffect(() => { void applyBlur(isCameraEnabled ? camTrack : null, blur.level); }, [camTrack, isCameraEnabled, blur.level]);
  const speakingIds = new Set(speaking.map((p) => p.identity));
  const people: CallPerson[] = participants.filter((p) => !LISTENER.test(p.identity)).map((p) => {
    const cam = cams.find((t) => t.participant.identity === p.identity && t.source === Track.Source.Camera && !t.publication.isMuted);
    return {
      id: p.identity, name: p.name || p.identity, self: p.isLocal, speaking: speakingIds.has(p.identity),
      muted: !p.isMicrophoneEnabled, camera: !!cam,
      feed: cam ? <VideoTrack trackRef={cam} /> : undefined,
    };
  });
  const screen = cams.find((t) => t.source === Track.Source.ScreenShare);
  const active = speaking.find((p) => !LISTENER.test(p.identity))?.identity;
  // An untranscribed call (a private or incognito chat's) has no meeting: no notes, no transcript.
  // ADD PEOPLE BY LINK (founder 2026-10-16): this call's chat join link, a day, revocable
  const [linkOn, setLinkOn] = useState(() => hasCallInvite(call.session));
  const menu: CallMenuItem[] = [
    { id: "invite-link", label: "Copy invite link", hint: "Anyone signed in who opens it joins this chat and its call; expires in a day",
      onSelect: () => { void copyCallInvite(call.session).then((r) => { toast(r === "copied" ? "Link copied" : "Couldn't make a link just now"); setLinkOn(hasCallInvite(call.session)); }); } },
    ...(linkOn ? [{ id: "invite-stop", label: "Stop link", hint: "The link stops working for anyone who has not used it",
      onSelect: () => { void stopCallInvite(call.session).then((ok) => { toast(ok ? "Link stopped" : "Couldn't stop the link"); setLinkOn(hasCallInvite(call.session)); }); } }] : []),
    ...(call.join.call.meeting_id ? [{ id: "meeting", label: "Notes and transcript", hint: "The meeting's view: its note, the transcript, your private chat about it", onSelect: onOpenMeeting ?? onPrivate }] : []),
    ...(onAddPeople ? [{ id: "people", label: "Add people", hint: "Mention someone in this chat to share it with them", onSelect: onAddPeople }] : []),
    ...(listener ? [] : [{ id: "blur", label: `Background blur: ${BLUR_WORD[blur.level]}`, hint: `Press for ${BLUR_WORD[nextBlur(blur.level)].toLowerCase()}`, onSelect: () => { cycleBlur(); } }]),
  ];
  return (<>
    <CallBar selfAction={listener ? undefined : <BlurButton />} name={nameWith ? nameWith(people.filter((x) => !x.self).map((x) => x.name)) : name} onName={onName} elapsed={elapsed} people={people} activeId={active} transcribed={transcribed}
      mic={isMicrophoneEnabled} cam={isCameraEnabled} sharing={isScreenShareEnabled} canPublish={!listener} narrow={narrow}
      onMic={() => void localParticipant.setMicrophoneEnabled(!isMicrophoneEnabled)}
      onCam={() => void localParticipant.setCameraEnabled(!isCameraEnabled)}
      onShare={() => void localParticipant.setScreenShareEnabled(!isScreenShareEnabled).catch(() => {})}
      onLeave={onLeave} menu={menu}
      devices={listener ? undefined : <>
        <label>Microphone<MediaDeviceSelect kind="audioinput" /></label>
        <label>Camera<MediaDeviceSelect kind="videoinput" /></label>
        <label>Speaker<MediaDeviceSelect kind="audiooutput" /></label>
      </>}
      share={screen ? { by: screen.participant.identity, byName: screen.participant.isLocal ? "You" : (screen.participant.name || "Someone"),
        title: "Screen", content: <VideoTrack trackRef={screen} /> } : null} />
    <BlurNotice />
    {/* A ROOM CALL (R1, `rooms`): Vexa has its own tile — it listens; it speaks when addressed */}
    {roomCall && transcribed && <div className="vx-ck-withvexa">
      <CallLane room={call.join.call.room} guests={guestsOn(people.filter((p) => !p.self).map((p) => ({ id: p.id, name: p.name })), members)}
        items={lane.items} onDecided={lane.reload} onTranscript={call.join.call.meeting_id ? (onOpenMeeting ?? onPrivate) : undefined} />
      <figure className="vx-ck-vexatile" data-call-vexa="" aria-label="Vexa is in this call, listening">
        <VexaMark size={36} /><figcaption><AgentChip state="listening" compact /></figcaption></figure></div>}
  </>);
}

"use client";
/** The call dock — the call stays on screen while you move between chats (ADR-0048 decision 6).
 *
 *  It is the top of the conversation column, one compact bar (UI decisions 7–10a, 14): the call's
 *  one name, the time, who is in it, the controls. The shared chat is the session the call belongs
 *  to and the agent there follows the live transcript; the bar's name goes back to it. "Notes and
 *  transcript" opens the meeting's own view (the transcript lives only in its sidebar drawer).
 *  Talking to the room in writing is the chat's composer — a shared chat is one stream.
 *
 *  The LiveKit components load only when a call starts, so a deployment without calls never fetches
 *  them. */
import { lazy, Suspense } from "react";
import { useFeature } from "../platform/features";
import { OPEN_PAGE_EVENT } from "../platform/events";
import { meetingDisplayName } from "./callsList";
import { leaveCall, useActiveCall } from "./callState";
import { CallShowBar } from "./CallShowBar";
import "./calls.css";

const CallRoom = lazy(() => import("./CallRoom"));

type DockProps = {
  /** go back to the call's shared chat */
  onOpenShared: (session: string) => void;
  /** open my own chat bound to this call's meeting */
  onOpenPrivate: (meetingId: number, room: string) => void;
  /** the chat's own name for a session — the dock never shows a raw id */
  nameOf?: (session: string) => string;
};

/** Hidden while `calls` is switched off (`platform/features`). */
export function CallDock(p: DockProps) {
  return useFeature("calls") ? <CallDockBody {...p} /> : null;
}

/** ADD PEOPLE is the chat's own @ (Participants: people are added by mentioning them): the composer
 *  of the open chat gets an "@" and the focus, which opens its people picker. */
function mentionInComposer() {
  const box = document.querySelector<HTMLTextAreaElement>('[data-pane="conversation"] textarea');
  if (!box) return;
  const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  const v = box.value;
  set?.call(box, `${v}${v && !/\s$/.test(v) ? " " : ""}@`);
  box.dispatchEvent(new Event("input", { bubbles: true }));
  box.focus();
}

function CallDockBody({ onOpenShared, onOpenPrivate, nameOf }: DockProps) {
  const { current } = useActiveCall();
  const show = useFeature("call_show");
  if (!current) return null;
  const c = current;
  // THE CALL'S ONE NAME (UI decisions 14): its title, else its chat's name, else the people — never
  // "Call · New chat" and never an id (`meetingDisplayName` refuses placeholders and raw ids).
  const nameWith = (people: string[] = []) => meetingDisplayName(
    { id: c.join.call.meeting_id, platform: "vexa_call", native_id: c.join.call.room, title_custom: null } as never,
    { chatName: nameOf?.(c.session) || c.label, people });
  const name = nameWith();
  // DOCKED, NOT FLOATING (live app.dev: the call card covered the meeting's transcript drawer): it is
  // the top of the conversation column.
  return (
    <aside aria-label={`Call: ${name}`} data-call="dock" className="vx-calldock vx-calldock-docked">
      <Suspense fallback={<div className="vx-call-meta">Connecting…</div>}>
        <CallRoom call={c} name={name} nameWith={nameWith} onLeave={leaveCall} onName={() => onOpenShared(c.session)}
          onPrivate={() => { if (c.join.call.meeting_id) onOpenPrivate(c.join.call.meeting_id, c.join.call.room); }}
          onAddPeople={mentionInComposer}
          onOpenMeeting={() => window.dispatchEvent(new CustomEvent(OPEN_PAGE_EVENT, { detail: { path: `meeting:${c.join.call.meeting_id}` } }))} />
      </Suspense>
      {show && <CallShowBar />}
    </aside>
  );
}

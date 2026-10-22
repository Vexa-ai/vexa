/** THE CHAT A LIVE CALL WAKES — the meeting's attachment (agent-api, behind the gateway).
 *
 *  One chat per live meeting row is woken by what is said in the call ("Hey Vexa", a listen rule).
 *  Any reader of the meeting may move it to one of their own chats; only the meeting's owner may let
 *  that chat speak in the call or post in the call's chat. `subject` is never sent — the gateway
 *  signs it (P20). A reader who cannot read the meeting gets a 404, which reads as `null` here. */
import { ApiError, getJson } from "./apiClient";

export interface MeetingAttachment {
  row: string;
  attached: boolean;
  /** attached to the chat this read was asked for */
  attached_here: boolean;
  /** the attached chat's name, when the reader may see it */
  chat_title?: string;
  /** the attached chat is the reader's own */
  mine: boolean;
  how: "sent" | "auto" | "chose";
  speak: boolean;
  post_chat: boolean;
  /** the reader owns the meeting */
  owner: boolean;
}

const url = (row: string, session?: string) =>
  `/api/meeting/${encodeURIComponent(row)}/attachment${session ? `?session=${encodeURIComponent(session)}` : ""}`;

/** agent-api's answer: the attached chat as `{session, title}`, only when it is the reader's own. */
interface AttachmentWire extends Omit<MeetingAttachment, "row" | "chat_title"> {
  meeting: string;
  chat?: { session: string; title?: string } | null;
}

/** The wire shape adapted here, at the client boundary. A chat with no name of its own (its title is
 *  its id) is named by the banner, not by its id. */
export function fromWire(w: AttachmentWire): MeetingAttachment {
  const { meeting, chat, ...rest } = w;
  const title = chat?.title && chat.title !== chat.session ? chat.title : undefined;
  return { ...rest, row: String(meeting), ...(title ? { chat_title: title } : {}) };
}

const orNull = async (p: Promise<AttachmentWire>): Promise<MeetingAttachment | null> => {
  try { return fromWire(await p); } catch (e) { if (e instanceof ApiError && e.status === 404) return null; throw e; }
};

/** The attachment as this reader sees it, for chat `session`; null when they cannot read the meeting. */
export function readMeetingAttachment(row: string, session: string): Promise<MeetingAttachment | null> {
  return orNull(getJson<AttachmentWire>(url(row, session)));
}

/** Move the attachment to chat `session` ("Listen here"). */
export function attachMeetingHere(row: string, session: string): Promise<MeetingAttachment | null> {
  return orNull(getJson<AttachmentWire>(url(row), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session }),
  }));
}

/** The owner's opt-ins for this call: answer out loud, post in the call's chat. */
export function setMeetingAttachmentOptIns(row: string, patch: { speak?: boolean; post_chat?: boolean }): Promise<MeetingAttachment | null> {
  return orNull(getJson<AttachmentWire>(url(row), {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
  }));
}

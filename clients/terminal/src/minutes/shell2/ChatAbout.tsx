"use client";
/** "Chat about this" / "Resume chat · n" — in the artifact's own header actions (UI 7q: never in the
 *  panel's tab bar, which holds navigation only). Feature `shell2`. */
import { MessageSquarePlus, MessageSquareText } from "lucide-react";
import { Button } from "../../ui-kit";
import { OPEN_SESSION_EVENT } from "../../platform/events";
import { CHAT_ABOUT_EVENT, holdPanel, useAboutChats, type Artifact } from "./about";

export function ChatAboutButton({ a }: { a: Artifact }) {
  const chats = useAboutChats(a.key);
  const have = chats[0];
  const go = () => {
    holdPanel();
    if (have) window.dispatchEvent(new CustomEvent(OPEN_SESSION_EVENT, { detail: { session: have.session } }));
    else window.dispatchEvent(new CustomEvent(CHAT_ABOUT_EVENT, { detail: a }));
  };
  return <Button variant="ghost" data-chat-about={a.key} data-resume={have ? "" : undefined}
    icon={have ? <MessageSquareText size={14} /> : <MessageSquarePlus size={14} />} onClick={go}>
    {have ? `Resume chat · ${chats.length}` : "Chat about this"}</Button>;
}

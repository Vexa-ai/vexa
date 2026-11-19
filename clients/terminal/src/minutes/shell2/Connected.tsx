"use client";
/** CONNECTED, TWO-WAY (UI 7p; feature `shell2`): at the foot of a page, the chats about it — each ONE
 *  clickable row (icon + name; never a label plus a separate "Open chat"). A click opens the chat in
 *  the centre and the panel keeps its place. The pages that link here stay in "Mentioned in", which
 *  is already one chip per page. Shown only when there is something. */
import { MessageSquare } from "lucide-react";
import { ListRow, SectionLabel } from "../../ui-kit";
import { OPEN_SESSION_EVENT } from "../../platform/events";
import { chatLabel, holdPanel, useAboutChats, type Artifact } from "./about";

export function Connected({ a }: { a: Artifact }) {
  const chats = useAboutChats(a.key);
  if (!chats.length) return null;
  return (
    <section className="vx-s2-connected" aria-label="Connected" data-connected={a.key}>
      <SectionLabel as="h3">Connected</SectionLabel>
      {chats.map((c) => <ListRow key={c.session} leading={<MessageSquare size={14} strokeWidth={1.75} />}
        title={<><span className="vx-s2-conn-pre">Chat about this ·</span> {chatLabel(c.session)}</>}
        onSelect={() => { holdPanel(); window.dispatchEvent(new CustomEvent(OPEN_SESSION_EVENT, { detail: { session: c.session } })); }} />)}
    </section>
  );
}

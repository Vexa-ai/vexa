/** The shell's selection model. The rail is ONE flat list of chats, so a selection is always a chat;
 *  `kind` only records whether that chat is ABOUT a meeting — which is the single fact the room
 *  layout keys off (prep vs has-transcript pages, transcript on the right). */
export type Sel = {
  kind: "chat" | "meeting";
  chatId: string;         // the chat record's id — also the rail's selection key and the agent session
  meetingId?: string;     // set iff kind === "meeting"
  label: string;
  workspaces: string[];   // the mount set, which now lives on the chat
};
/** A tab. `kind` absent = a document, which is what every tab was before the transcript stopped
 *  being a file — so a chat persisted by an older build migrates by meaning nothing. A `meeting`
 *  tab's `path` is the meeting ROW ID, not a workspace path. */
export type Page = { kind?: "doc" | "meeting"; path: string; slug?: string; label: string };

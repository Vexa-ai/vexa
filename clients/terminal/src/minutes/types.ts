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
export type Page = { path: string; slug?: string; label: string };

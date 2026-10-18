/** The shell's selection model. The rail is ONE flat list of chats, so a selection is always a chat;
 *  `kind` only records whether that chat is ABOUT a meeting — which is the single fact the room
 *  layout keys off (prep vs has-transcript pages, transcript on the right). */
export type Sel = {
  kind: "chat" | "meeting";
  chatId: string;         // the chat record's id — also the rail's selection key and the agent session
  meetingId?: string;     // set iff kind === "meeting"
  label: string;
  workspaces: string[];   // the mount set, which now lives on the chat
  /** WHERE THIS CHAT WRITES (Vexa-ai/vexa#1611). One of `workspaces`, or absent for the person's
   *  own desk — the default. `workspaces` is what the chat can REACH; this is where it works, and
   *  the two were one field until the founder watched files land on his desk from a chat about a
   *  customer: *"it creates files in the wrong workspace, we need so that the thing knew the
   *  workspace of writing, if it's specified"*. */
  target?: string;
};
/** A tab. `kind` absent = a document, which is what every tab was before the transcript stopped
 *  being a file — so a chat persisted by an older build migrates by meaning nothing. A `meeting`
 *  tab's `path` is the meeting ROW ID, not a workspace path. */
/** One entry in the pages panel's strip.
 *
 *  It carries the strip's three state fields as well as the document's identity, because the strip
 *  IS the chat's `artifacts[]` (decision 18) and this type is what the panel passes around. They
 *  were absent, and that absence is what let the persist writer drop `at`/`desk` and stamp
 *  `pinned: true` on everything WITHOUT A TYPE ERROR — nullifying decisions 28, 28.4 and 28.5 while
 *  every signature still looked right. `Artifact` in chats.ts is the same shape; the two are
 *  deliberately structurally compatible so a page and a stored artifact can pass for each other. */
export type Page = {
  kind?: "doc" | "meeting"; path: string; slug?: string; label: string;
  /** the chat's home — first in the strip, never forgotten, never evicted */
  desk?: boolean;
  /** asked for: a pin, an explicit open-in-tab, or a scaffold's declared tab */
  pinned?: boolean;
  /** when this page was last in front — the strip's order, and what the cap evicts on */
  at?: number;
  /** THE MEETING'S OWN PAGE — a tab that cannot be closed (Vexa-ai/vexa#1600).
   *
   *  Founder, 2026-09-06, on the "Open transcript" chip a meeting chat used to carry: *"just keep a
   *  tab that can't be closed instead"*. The transcript and the meeting's page belong to the
   *  MEETING, not to the reader's tab habits — so the tab renders no close control and every close
   *  path refuses it, which is what makes the chip unnecessary rather than merely redundant.
   *
   *  Stamped by `meetingPages()` — the ONE writer of what a meeting shows — and by nothing else. A
   *  pin stays an ordinary pin: pinning is the reader saying "keep this", and what the reader kept
   *  the reader may drop. */
  permanent?: boolean;
};

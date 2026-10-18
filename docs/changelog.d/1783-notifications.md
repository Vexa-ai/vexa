- **Notifications, and chats shared with you in your chat list.** When someone adds you to a chat or
  shares one with you, mentions you, invites you to a workspace or adds you to one, accepts or
  declines your invite, starts a call or a routine posts a run in a chat you are in, a notice shows at
  the top right for six seconds (click it to open the chat or workspace), and the bell in the chat
  list's header counts what you have not read. A chat shared with you appears in your list at once in
  a new built-in **Shared with me** section, with a **shared** chip and the owner's initials; you can
  drag it into your own folders. Leaving a chat, or being removed, takes it off your list. Notices name
  who did what to which chat or workspace, never what was said. The agent can answer *"what did I
  miss?"* with the new tools `notifications_list` and `notifications_mark_read`. An invite sent to you
  can now be declined (`POST /agent/workspace/invites/decline`). Kept by agent-api in the session
  store (one new table, `agent_session_notification`) and delivered live over the existing `/ws`
  socket on each person's own channel. See [Notifications](/minutes/notifications).
- **Agents can notify you.** The new `notify` tool lets an agent, in a chat or a routine's run, post
  one line to your bell (kind `agent`), linking to its chat or to a page or meeting; in a shared chat
  it may notify the other people in it, nobody else. At most 20 an hour, one per routine run, no
  repeats within ten minutes; off with `agent_notifications: off` in `_global/POLICIES.md`. A routine
  that says to notify you of important mail now does.

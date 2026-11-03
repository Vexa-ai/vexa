- **Routines are flows, and each routine is a chat (#1783).** A routine is now a flow of your own: a
  schedule and one agent turn. Each run lands in the routine's own thread in your chat list, carries
  the earlier runs, and reads your replies. You can pause, resume or retire it from the thread.
  Flows owns every schedule. A slot fires at most once, and after downtime only the most recent
  missed slot runs, if it is under an hour old (the default). Your flows are yours alone, limited to the steps the instance allows, and capped
  per person. Existing routines are converted once on upgrade, and the `/api/routines` routes stay
  until 0.14.0 as deprecated adapters. See [Routines and agent messages](/flows/routines).
- **Agents can message each other (#1783).** The `message_agent` tool sends a message to another
  chat thread. It arrives as a turn marked with its sender, and the answer comes back later in the
  sender's thread. `_global/POLICIES.md` decides who may message whom: by default your own threads
  freely, other people not at all, and not from routines. With `agent_messages_shared` switched on,
  a message reaches another person only in a conversation you both take part in and you can write
  to, inside a workspace you share where you are not a reader; their own chats and routines are
  never an address. A loop guard and a daily count bound every exchange.

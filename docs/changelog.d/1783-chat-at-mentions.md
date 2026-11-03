- **Chats: add people with @.** Type **@** in a message to pick someone you share a workspace with. On
  Enter, Vexa asks once whether to share the chat with them: **Share and send**, **Send without
  sharing**, or **Cancel**. A private chat becomes a new shared chat that starts with that message;
  what you said before stays private. The bar above the chat now only shows who is in it.
- **Share by asking the agent.** *Share Acme with @Joe and share this chat with him* does both. New
  agent tools: `workspace_member_add`, `chat_participants`, `chat_participant_add`,
  `chat_participant_remove`. They need you present, and only owners add.
- **Routines' chats can be shared.** The routine stays its owner's. Its runs in the shared chat use
  only what everyone there can read, and the share question says first if the routine reads your
  private connections.

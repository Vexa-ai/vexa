- **Rooms and shared chats reach only declared routes (0.13.4 security floor, D3).** Every route in
  every domain's `routes.v1.json` declares `shared: allow|refuse`. The gateway refuses a turn other
  people read on every route not declared `allow`, and agent-api and identity check again. A room
  keeps its workspace's pages, its call, proposing a claim, one meeting's transcript and the CRM
  reads. Person-wide routes answer `shared_conversation`, for example follow-ups, contacts, claims,
  home, notifications, usage, other chats, references, the person's meetings and identity settings.
  The MCP edge and the worker's toolbelt hide the same tools, and an undeclared route fails CI. See
  [Shared chats](/minutes/shared-chats).
- **A room can run as its workspace (D2, off by default).** With `VEXA_ROOM_WS_PRINCIPAL=true`, a
  room's token names the workspace's own numeric principal (identity's new `principals` table) with
  the speaker as `on_behalf_of`. identity answers the owner as `bills_to` and never answers the
  principal as the admin. See [Identity and trust](/architecture/identity-and-trust).
- **Other members' messages reach the agent fenced as untrusted (M3), and query-string keys no
  longer reach the gateway's logs (M7).**

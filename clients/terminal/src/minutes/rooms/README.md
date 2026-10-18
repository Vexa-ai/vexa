# terminal/src/minutes/rooms — rooms and private chats (R1, #1783)

Behind the `rooms` feature (and `home2` for the sidebar); both are off by default and are never
switched on by `all`. Rooms are made in a workspace and are open by default. Chats stay private: they
are always one person's own agent (founder 2026-10-15). The server side is agent-api's
`routers/rooms.py`; the data access is `surfaces/roomsApi.ts`, and the room state every reader shares
is `surfaces/roomState.ts`.

- `RoomHeader.tsx` — the room's header, cut to essentials: the workspace's mark, # or a lock and the
  name, one quiet line (workspace · members · Vexa's setting), then members, Call and "…". The "…"
  holds Vexa's setting, Schedule a call, Context, Think privately, Activity, Room settings and Leave. A room has no
  "Writes to" chip because it always writes to its workspace. `RoomSettingsDialog` renames the room
  and changes it between open and closed; opening a closed room says who will see its history and
  asks again before it sends `confirm_history`.
- `RoomDialogs.tsx` — New room (in a workspace, open or closed), "Take to a room…" (the same dialog,
  named after the chat; the chat itself stays private), and Browse open rooms (join an open room of a
  workspace you are in).
- `ScheduleCallDialog.tsx` — Schedule a call: title, time, length, invitees by email and an optional
  agenda page. The room plans the call (`POST /api/rooms/{s}/plan`); nothing is sent. The creator adds
  it to their own calendar (the `.ics`, or the invite copied), so invitations come from them.
- `CallLane.tsx` + `laneData.ts` — a room call's lane, beside Vexa's tile: how Vexa writes on this
  call (members only: it updates the workspace; with a guest — anyone on the call who is not a room
  member — it proposes), what it proposed since the call started (D1's `/api/changes?room=`,
  Accept / Decline), what it changed (the call's change list, `/api/calls/{room}/changes`, each with
  Undo; a refusal says the server's words), and Transcript (the meeting in the right panel).
- `WorkspaceMark.tsx` — a workspace's monogram on its own colour, until workspace images land.
- `Face.tsx` — your face in a personal chat's header, plus the link a "Think privately" chat keeps
  back to its room (this browser's record).
- `autoTitles.ts` — the sidebar never shows "Chat 12:30" (`home2`): an unnamed chat takes its first
  sentence.
- `rooms.css` — tokens only, light and dark from the theme.

The shell (`MinutesShell.tsx`) draws `RoomHeader` for a room and the personal header for a chat;
`Rail.tsx` groups the sidebar by workspace (`home2`) and offers New room and Browse open rooms; the
composer offers @ only in a room; `CallRoom.tsx` gives Vexa a tile in a room call.

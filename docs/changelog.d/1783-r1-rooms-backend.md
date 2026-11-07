- **Rooms, backend (feature `rooms`, off; not part of `all`).**
  - A room belongs to a workspace and has its own model level. Its write target is fixed to that
    workspace.
  - **The room agent.** A room's turns run as the workspace's agent, one worker per room, with the
    workspace read-write and its connected open workspaces read-only. Nobody's own desk, mail,
    calendar or connections are in it, and its turns are paid from the workspace owner's pool.
  - **Outsiders.** When a room has someone from outside the organisation, the CRM answers it with
    public fields only.
  - **Workspaces.** Owners can open a workspace; open workspaces can be connected to another
    workspace's rooms.
  - **Folders.** Chat folders migrate to workspaces, or to Hidden. The migration has a dry run, and
    nothing is deleted.
  - **Calls.** A call started in a room belongs to the room, and its recap lands there. A room can
    plan a call. Vexa then prepares the calendar event and an `.ics` file, and the person sends it
    from their own calendar.
  - **Edits during a call.** Vexa's edits during a call are listed and can be undone. If someone
    changed the page since, a restore proposal is made instead.
  - *How it works / why it complies:* the room's mount table and its token's ceiling are the
    agent's grant, checked at every dispatch. A turn in a room is always marked shared, and when an
    outsider is present it is marked cross-org. Every write by the agent goes through the page
    write path and can be reverted with an attributed commit.
- A call change names the person the room turn answered (`on_behalf_of`), recorded by agent-api for each dispatched room turn, never taken from the worker.
- A room turn spoken by a reader of the workspace runs with the workspace read-only, on a sibling worker of its own; making a room, moving one into a workspace and choosing its model need a writer of that workspace; the room runs on its owner's authority only while `rooms` is on for the owner too.
- A room connects only open workspaces of its own organisation; the planned call's calendar file carries only well-formed link and attendee lines.

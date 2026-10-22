- **Every workspace wears a standard icon (0.13.4).** A new workspace gets an outline icon matched
  from its name, and you can pick another from the icon grid under Change icon. A workspace with no
  image of its own shows its icon instead of a monogram. See [Workspace images](/workspace-identity).
- **A routine an agent proposes is one press from you.** It shows as `Draft routine · <name> · Turn on`
  at the top of the chat it runs in, and under Routines in the sidebar. Turning it on still needs
  your signed-in session; a routine filed with an API key is a draft too.
- **References name the thing for whoever reads them.** `[[meeting:<id>]]` and the other references
  store no name; each badge resolves the title, time and owner for its reader, and an unreadable one
  names nothing. People are shown by the name on their profile, never by an id or a mailbox handle.
- **A meeting your message names is material, not instructions.** Its title, participants and
  transcript reach the agent one line each, marked as what the meeting says, so a line in a meeting
  cannot pose as an instruction or another reference. See [Shared chats](/minutes/shared-chats).
- **Helm: `meetingApi.calls.transcribeSharedScreen: false` turns shared-screen transcription off.**
  Before, Helm rendered an explicit `false` as `"true"`.
- **Smaller fixes.** A call started in a room has no second row in the sidebar; the model chip names
  the effort your next turn runs at and offers No thinking on models that support it; Home opens at
  its top.

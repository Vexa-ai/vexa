- **Minutes · a voice note starts from a suggestion or from the agent.** An empty chat suggests
  *Record a voice note*, and asking the agent to record one opens the recorder in your tab through the
  new MCP tool `record_voice_note` (`POST /voice-notes/recorder`, any API key). The tool records
  nothing itself and is refused in a run with nobody present. The chats rail no longer has a
  microphone button; it still shows a running clock, with Stop, while a note records. The composer's
  microphone is unchanged: it dictates into the message box.
- **Minutes · suggestions have icons.** Every empty-chat suggestion leads with an icon: a calendar for
  meeting prep, mail for the email check, a plug for connecting an account, a microphone for a voice
  note.
- **Minutes · a recording voice note reads as one.** In the pages pane the note's page takes the pane's
  width (centred), shows *Recording m:ss* with Pause and Stop in its header, gives the running
  transcript most of the page, counts the line still being said, labels it with your name, and says the
  notes come when you stop instead of offering *Create notes* mid-recording.

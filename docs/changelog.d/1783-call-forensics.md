- **Private sources stay out of shared workspaces (#1783).** After the agent reads your mail,
  calendar or a connected service, it writes nothing into a workspace someone else reads unless you
  confirm that page with `confirm <code>`; it tells you what it is holding back.
- **A meeting's notes are filled when it ends (#1783).** The post-meeting report now also fills the
  note's empty Decisions, Commitments and Open questions, and a note still called "Google Meet
  meeting" takes the meeting's real name.
- **Transcription stays in the languages you speak (#1783).** With no language set, a bot now
  restricts detection to the languages your recent meetings were spoken in (for example Russian and
  English), so short windows are no longer transcribed as Portuguese or Czech.
- **Subtitle credits no longer appear in Google Meet transcripts (#1783).** Lines such as
  "Редактор субтитров …" or "Subtitles by …" are dropped, and outros such as "Thanks for watching"
  are dropped when no speech was heard; the Zoom/Teams pipeline already did this.
- **Meeting notes are written from the whole meeting (#1783).** The transcript tool answers in pages
  of 200 lines and says when there is more; Update notes and the post-meeting report read every page,
  and never write "not discussed" about something they did not read.
- **A meeting bundle says what the meeting was, and carries no tokens (#1783).** Bundles (format
  1.1) carry the duration, the meeting link without its query, the calendar event, the workspace,
  the transcription language and the bot's timeline; invite links and passcodes in pages, notes and
  annotations are replaced with `[removed]`. A deployment still on format 1.0 refuses a 1.1 bundle.
- **Entity pages stay tidy under parallel writes (#1783).** `## Connected` keeps one chip per page
  however it is spelled, entity writes to one workspace no longer interleave, and a page written at
  `kg/entities/<name>.md` is filed into its kind's folder (or listed as unfiled) in `kg/INDEX.md`.

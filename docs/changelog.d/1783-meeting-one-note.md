- **One note per meeting, shown live.** Update notes, a change asked for in any chat and the
  post-meeting report all write the meeting's one note, the one embedded in the meeting sidebar,
  even from a chat that works in another workspace, and even when the agent writes the file directly
  (the turn's commit merges it into the one note); the sidebar picks up any change within
  seconds. The note is edited by clicking it, starts empty (no placeholder title or bare
  headings), and says when an update found nothing to write. Opened from Files it is a plain page:
  no meeting header, Stop bot, language control or Extend card, and a turn that writes it brings
  the meeting view forward instead of opening the file in a tab.
- **One playback control.** The meeting sidebar has no separate "Play recording" button; the
  inline player (play/pause, time, scrubber) is the only one.
- **The meeting note reads like a page.** It renders with the page renderer (entity chips, no empty
  sections, no outline), is edited from "…" → Edit notes with the machine markers hidden, and
  updates link the people, companies, projects and meetings it names. The freshness line reads
  "Updated 09:23 · transcript to 14:02" (meeting time, not a clock). A meeting is named by its title,
  chat, or people ("Call with Valeriia"), never by its meeting id, with the platform as an icon. The
  collapsed transcript drawer shows only the latest line.
- **A meeting's note is found wherever it is.** With Personal switched off or deleted, new notes go
  to the chat's workspace, and a report left in Personal still shows in the sidebar, read-only.
- **A page is bound to its meeting by identity.** An agenda written before a call is adopted by
  the meeting when it names the meeting's link or native id (`meeting_attach_page`), or bound later
  from the page's "…" with **Use as meeting page…**; the sidebar, Update notes and the report all
  use it. One meeting, one page.

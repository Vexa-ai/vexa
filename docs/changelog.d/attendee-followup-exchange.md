- **Outlook and Exchange invites are read at all.** `DTSTART;TZID=W. Europe Standard Time` is the
  Windows spelling of a timezone, not the IANA one, and it raised out of the ICS parser, out of the
  mail router and out of the mailbox poll — so every Exchange invite was dropped by an exception
  rather than by a decision, and nothing downstream could say which meeting had gone missing. A
  timezone we cannot name is now treated as UTC, the same answer a floating start time already got.
- **With no agent, everyone in the room is told the meeting was recorded.** The person who invited
  Vexa already got that mail; every other attendee got silence, from a bot they had watched sit in
  the meeting. They now get the same words the organiser does — no summary claimed, no button into
  a chat that is not deployed.
- **A meeting can be kept off everyone else's desk.** Sharing notes with the attendees inside your
  organisation stays on by default; `#noshare` in the invite excludes one meeting. The minutes mail
  says both, so the choice is visible to the person who has it.

- **Yandex Telemost meetings: send a bot to a `telemost.yandex.ru/j/<id>` link (#1734).** A new
  `telemost` platform joins Telemost calls as a guest through the web client, captures the call audio on
  the mixed lane and names speakers from the call's own engine signal beside the speaking tiles — in the
  grid and with a screen shared. Links are recognised by the API, MCP `parse_meeting_link`, the Terminal
  and forwarded invites. Join, speaker names and end-to-end transcripts are witnessed live on a compose
  stack. See [Meetings API](/api/meetings).
- **Calendar import: the organiser's own link names the room, not the auto-attached Meet (#1734).**
  Google Calendar stamps a Meet conference on every event it creates, so a daily held in Yandex
  Telemost (or Zoom, Teams, Jitsi) with its link in LOCATION, URL or DESCRIPTION used to import as a
  Google Meet meeting and the bot sat in an empty Meet room. A link to any other platform on the event
  now wins over the Meet conference; an event whose only links are Meet is unchanged, and the
  iCalendar `URL` property is read as a link source. See [Calendar sync](/how-to/calendar-sync).

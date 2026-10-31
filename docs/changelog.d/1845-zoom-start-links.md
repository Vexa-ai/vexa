- **Zoom start links from a calendar no longer fail the bot (#1845).** A calendar shows a meeting's
  owner the host start link (`https://<tenant>.zoom.us/s/<id>?zak=…`) instead of the `/j/<id>`
  join link; the bot rejected it with "Cannot extract meeting ID from Zoom URL". The bot, the MCP
  `parse_meeting_link` tool, the forwarded-invite mailbox and the dashboard join form now accept
  `/s/<id>`. The bot joins as a guest: it keeps `pwd` and drops the host's `zak` start token.

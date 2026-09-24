- **Yandex Telemost meetings: send a bot to a `telemost.yandex.ru/j/<id>` link (#1734).** A new
  `telemost` platform joins Telemost calls as a guest through the web client, captures the call audio on
  the mixed lane and names speakers from the call's own engine signal beside the speaking tiles — in the
  grid and with a screen shared. Links are recognised by the API, MCP `parse_meeting_link`, the Terminal
  and forwarded invites. Join, speaker names and end-to-end transcripts are witnessed live on a compose
  stack. See [Meetings API](/api/meetings).

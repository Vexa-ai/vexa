- **MCP: `request_meeting_bot` can start a Zoom bot again (#1630).** The tool forwards the
  caller's full Zoom join link to the bots API, which needs the link's host to join; before, it
  kept only the numeric id and every Zoom request from an agent was refused with 422. Pass the
  full `meeting_url` for Zoom, as for Jitsi.

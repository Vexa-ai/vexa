- **Meeting links are matched on the exact hostname (#1553).** `parse_meeting_link` and the
  pasted-link/calendar parser used to decide the platform with a substring test, so
  `teams.live.com.evil.example` and `notzoom.us` were read as Teams and Zoom. They now match the
  hostname exactly, or as an explicit subdomain — vanity and government tenancies
  (`us02web.zoom.us`, `company.zoom.us`, `frbmeetings.zoomgov.com`, `contoso.teams.microsoft.com`)
  are unaffected, and a look-alike hostname is refused instead of sending a bot somewhere on its
  say-so. See [MCP tools](/mcp).

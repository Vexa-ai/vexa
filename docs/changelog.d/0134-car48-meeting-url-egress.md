- **A bot's meeting link must be the platform's, and its browser stays on public addresses.**
  `POST /bots` (and the MCP, agent and calendar auto-join paths that use it) now refuses a
  `meeting_url` whose host is not its platform's (Meet `meet.google.com`; Teams' web-client hosts;
  Zoom `zoom.us` and its subdomains; Jitsi and Zoom front doors on any host), or that resolves to a
  loopback, private, CGNAT, link-local/cloud-metadata or reserved address, with a 422. The bot's
  browser holds every navigation and redirect to the same rule and fails the join with a
  `meeting_host_refused` reason. An on-prem Jitsi is admitted by listing it in
  `VEXA_MEETING_HOST_ALLOW` (Helm `meetingApi.meetingHostAllow`); upgrade the bot image with
  meeting-api before setting it. See [Configuration](/configuration).

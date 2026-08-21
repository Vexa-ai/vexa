- **The MCP service matches meeting links to a platform by exact host, not by substring (#1169).**
  Its link parser used to recognise Zoom by looking for `zoom.us`/`zoomgov.com` anywhere in the
  hostname, and personal Teams links with a suffix test that was missing its leading dot — both
  also accepted hosts that merely began with the platform's name and belonged to whoever submitted
  the URL. Hosts now have to *be* the platform's domain or a subdomain of it, and a host that only
  looks like one can no longer slip back in through the self-hosted Jitsi naming heuristics. The
  terminal client's copy of the parser gets the same treatment. Sibling of #1168, which made the
  change in the meetings API. Nothing legitimate changes: `zoom.us` and its regional/vanity
  subdomains, `zoomgov.com`, `teams.live.com`, `teams.microsoft.com` with its gov/dod tenants,
  `meet.google.com`, `meet.jit.si`, the `meet.example.org` self-hosted convention and anything
  declared in `VEXA_JITSI_HOSTS` all parse exactly as before.

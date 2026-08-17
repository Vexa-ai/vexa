- **The join layer infers a meeting's platform from the link's hostname, not from a substring of the
  URL (#1170).** `resolvePlatform()` recognised Google Meet and Teams by looking for the platform's
  name anywhere in the meeting URL, so a host that merely carried that name — in a query string, or
  as a prefix of a domain whoever supplied the link registered — was classified as that platform and
  driven through its join flow. A host now has to *be* the platform's domain or a subdomain of it:
  the rule the Teams sign-in-redirect guard already applied, now shared by both. Sibling of #1168
  (meetings API) and #1169 (MCP and terminal client). Nothing legitimate changes — `meet.google.com`,
  `teams.microsoft.com` and `teams.live.com` with their tenant subdomains, `zoom.us` with its
  regional and vanity subdomains, `meet.jit.si` and `8x8.vc` all resolve exactly as before. Two links
  that used to be refused now resolve: Teams' gov/DoD (`teams.microsoft.us`) and `teams.cloud.microsoft`
  hosts, which this layer's Teams join flow already treated as the meeting, and a link whose host is
  written in uppercase.

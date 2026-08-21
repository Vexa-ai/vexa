- **Meeting links are matched to a platform by exact host, not by substring (#1168).** The link
  parser used to classify a URL by looking for `meet.google.com`, `zoom` or `teams.microsoft.com`
  anywhere in the hostname, which also accepted hosts that merely began with one of those names
  and belonged to whoever submitted the URL. Hosts now have to *be* the platform's domain or a
  subdomain of it, and Zoom is recognised on `zoom.us` / `zoomgov.com` rather than on the word
  "zoom". Self-hosted Jitsi is unaffected: `meet.jit.si`, jitsi-named hosts, the `meet.example.org`
  naming convention and anything declared in `VEXA_JITSI_HOSTS` parse exactly as before.

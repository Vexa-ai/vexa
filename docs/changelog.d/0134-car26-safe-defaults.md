- **Two 0.12 compatibility switches now ship closed.** `/ws` takes the REST key scopes by default
  (`VEXA_WS_SCOPE_MODE=strict`), so a `bot`-only key no longer streams transcripts. Per-client
  webhooks are delivered under the outbound-url.v1 guard by default
  (`VEXA_WEBHOOK_STORED_URL_POLICY=strict`), so 100.64/10 and the documentation and benchmarking
  ranges are refused. `legacy` and `v012` are explicit opt-ins, wired on Compose, Lite and Helm
  (`compat.*`). See [Compatibility switches](/upgrade-from-0-12#compatibility-switches).
- **Meeting import and restore are bounded.** An uploaded bundle is spooled to a temporary file, and
  recordings are checked as a stream instead of being held in memory. Each process reads at most two
  bundles at once and answers the next `429 busy` with `Retry-After`. See
  [Move a meeting](/how-to/move-a-meeting).
- **The bot-account sign-in browser stays on the platform's sign-in hosts.** A navigation anywhere
  else, including an internal service, `file://` or `chrome://`, is refused. A company identity
  provider can be added with `VEXA_BOT_SIGNIN_EXTRA_HOSTS`. See [Bot accounts](/minutes/bot-accounts).
- **Analytics never receives a query string.** GA's `page_location` and `page_referrer` carry the
  origin and path only, so a `/join?i=` invite never reaches it.
- **Helm: agent-api and the CRM start on a default install.** The agent-api session-schema
  initContainer and Job, the workspaces S3 migration Job and the CRM images fall back to
  `v<appVersion>`, like every other image. The `crm-schema` Job is a release resource, so it runs on
  `helm install`.

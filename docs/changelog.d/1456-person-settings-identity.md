- **A person's settings live in identity, not in their workspace (#1456).** Timezone and the mail
  switches are read from admin-api rather than from a `.settings.json` file in the workspace, so one
  answer serves every service. **Existing deployments must run the one-shot import** — an
  operator-triggered migration on admin-api reads the old files in — or everyone starts on the
  defaults: mail on, clock in UTC.

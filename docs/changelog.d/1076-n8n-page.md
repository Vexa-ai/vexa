- **Automate meeting summaries with n8n, documented (#1076).** New [n8n guide](/n8n) walks the
  no-code path end to end — calendar trigger, `POST /bots`, transcript fetch, summary, Slack — and
  states plainly that the published community template still points at the retired
  `gateway.dev.vexa.ai` base URL, which must be swapped for `api.cloud.vexa.ai` after import. Also
  covers the self-hosted reachability variants and the option to skip the calendar trigger entirely
  by using [Vexa's own calendar sync](/how-to/calendar-sync). The `/n8n` URL had been drawing steady
  traffic to a 404.

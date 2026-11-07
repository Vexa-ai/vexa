- **Links to emails and events open the connected account (#1812).** Gmail and Calendar reads now
  return a `web_url` built by the credential broker: a Gmail link names the connected mailbox's
  address instead of the browser's first account, and a Calendar link is Google's own event link.
  The agent links only with it and never builds a provider URL. See [Connections](/connections).

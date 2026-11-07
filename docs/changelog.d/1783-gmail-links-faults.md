- **Gmail links open the right message.** The `web_url` on Gmail results is now
  `https://mail.google.com/mail/?authuser=<connected address>#all/<thread id>`. The earlier form put
  the address in the path (`/mail/u/<address>/`), and Gmail answers that path with its own
  "Temporary Error (404)" page even for a signed-in person. Pages written with the old links need to
  be regenerated: ask the agent to refresh them. See [Connections](/connections).
- **A sign-in outage is named as Vexa's.** When the gateway cannot check a key it still answers
  `503` with `detail: "Authentication temporarily unavailable, retry"`, and the body now also carries
  `code: auth_unavailable`, `reason: vexa_sign_in_unavailable`, `source: vexa` and a sentence saying it
  is Vexa's own sign-in service, not the person's account and not Google or Microsoft. The agent is
  told to blame a provider only when the provider returned the error, to suspect its own link first
  when a link does not open, and never to fetch Gmail or Calendar web pages to test a link.
- **"It does not fix" is answered, not filed.** When a person says something did not work, the agent
  first re-checks what failed and answers. It files `report_friction` only when asked, or once the
  failure is confirmed as Vexa's.
- **Sign-in checks no longer wait on writes or locks.** admin-api's key check (`/internal/validate`)
  stamps a key's last use at most once a minute, skips a row another transaction holds, and never
  fails the answer when the stamp cannot be written. Its reads carry a 3 s `statement_timeout` and a
  1 s `lock_timeout`, so a stuck database is answered with the gateway's typed `503` in seconds
  instead of holding the request for half a minute. admin-api's startup schema convergence asks for
  each lock with a 500 ms `lock_timeout` and retries with backoff, so live sign-ins never queue
  behind a migration waiting for a lock.

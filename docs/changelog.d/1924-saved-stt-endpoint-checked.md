- **A personal transcription endpoint is checked: when it is saved, and again when a bot is sent (#1924).**
  `PUT /user/transcription` sends one short audio request to the endpoint you'd end up with, through
  the outbound URL guard with no redirects. A rejected token, an account that can't pay, a wrong path
  or an internal address is refused with `422` (`transcription_endpoint_rejected`, a `kind` naming the
  cause) and nothing is saved; a working endpoint saves with `probe: {"ok": true}`; one that can't be
  reached still saves, and the response says so (`probe.kind: "unreachable"`). Later, `POST /bots`
  checks the saved endpoint itself — not the deployment's — and refuses with a typed error before any
  meeting row exists when it has stopped working, so a bot no longer joins and goes deaf. On 0.12 a
  saved endpoint was never checked. See [Settings](/api/settings#transcription-backend).

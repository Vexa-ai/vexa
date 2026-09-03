# tests/mailpit — the recorded inbound double

`messages.json` is the shape mailpit **1.30.7** actually returns from `GET /api/v1/messages`
(recorded off the dogfood rig): newest first, `ID` a random base62 string with no order in it,
`Created` in Go's RFC3339Nano — which trims trailing zeros, so `.5Z` sits next to `.503Z` and
string comparison would order the two wrongly. That trap is deliberate; `test_inbound_double.py`
pins it.

The three `.eml` files are what `GET /api/v1/message/<ID>/raw` returns for those rows:

| file | what it proves |
|---|---|
| `invite-dna-tsc.eml` | an ICS invite the DNA corpus shape: a **Zoom** URL, two real ATTENDEEs plus our own address, a `#group:` tag, and folded lines (RFC 5545) through the ATTENDEE and DESCRIPTION properties |
| `reply-minutes.eml` | a reply carrying `In-Reply-To` — routed by the thread row, never by the sender |
| `not-for-us.eml` | mailpit accepts every address, so a poller that does not filter on `VEXA_MAIL_ADDR` answers another tenant's mail |

Re-record with `curl -s "$VEXA_MAILPIT_URL/api/v1/messages?limit=200"` and
`curl -s "$VEXA_MAILPIT_URL/api/v1/message/<ID>/raw"`.

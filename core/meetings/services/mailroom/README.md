# mailroom — meetings enter by email invitation (Python, Stage-0 dev)

## Purpose

**A workspace has an email address. Users invite it to a meeting like any other attendee. The
`.ics` lands in *our* mailbox, and the invited address IS the workspace resolution.** No calendar
integration: no Graph, no EWS, no iCal feed, no read access to anyone's calendar, ever — the only
thing this service reads is its own inbox.

That inverts the setup problem. Every calendar-integration design needs read access to the
*user's* calendar, which is an internal approval in most organizations and a non-starter in
regulated ones. Inviting an address is something anyone can already do from any client.

| | |
|---|---|
| **In** | invitations delivered to the workspace mailbox (Mailpit in dev, behind a port) |
| **Out** | planned meetings on the **public** meeting API (`POST` / `PATCH` / `DELETE /meetings`) |
| **Holds** | the series↔meeting binding, the resume cursor, and the notice log |
| **Never** | sends mail · reads a user's calendar · writes meeting-api's database · broadcasts |

## The rules

- **The invited address is the resolution** — the ICS `ATTENDEE` list, not the SMTP envelope. An
  invitation that merely *reached* the mailbox (forwarded, BCC'd) does not bind: otherwise
  forwarding an invite would put a bot in a stranger's meeting. It reached us, so its organizer
  gets an explanation; it did not invite us, so nothing happens.
- **A recurring invitation binds the series.** One binding, one planned row, and the row moves to
  the next occurrence as each one passes (`advance_series`) — a recurring invite is sent once, so
  nothing else would keep it alive.
- **Updates and cancellations are honoured.** `SEQUENCE` is the update counter: a higher one
  re-schedules the same row, a same-or-lower one is an out-of-order delivery and does nothing —
  including after a cancel, so a stale copy can never resurrect a called-off meeting.
- **Fail-safe, never broadcast.** Anything unresolvable — a malformed `.ics`, an invitation with
  no joinable link, a floating `DTSTART`, an address we do not know, a control plane that refuses —
  produces **no group effect** and a recorded **notice**. v0 records notices; it sends nothing.
- **At-most-once.** Mail is delivered at-least-once; the same UID+`SEQUENCE` is acted on once,
  across restarts.

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| consumes | the inbound mailbox (`MAILPIT_URL`) | `GET /api/v1/messages` · `GET /api/v1/message/{id}/raw` | RFC-822 bytes + an arrival stamp |
| consumes | the public API (`MEETING_API_URL`, the gateway) | `POST /meetings` · `PATCH /meetings/{id}` · `DELETE /meetings/{id}` | a planned meeting: link, title, start, workspace, `auto_join` |
| serves | the operator | `GET /health` · `POST /internal/poll` · `GET /internal/bindings` · `GET /internal/notices` | liveness + what the mailbox did |

The mailroom is a **consumer** of the meetings control plane, exactly like any customer
integration: it holds an API key, calls published routes, and imports nothing from `meeting-api`
(P2/gate:isolation-py — the meeting-link parser is vendored, as `vexa_mcp` vendors its own).

## Configuration

| key | default | what |
|---|---|---|
| `MAILPIT_URL` | `http://mailpit:8025` | the dev inbound mailbox's HTTP API |
| `MEETING_API_URL` | `http://gateway:8000` | the public API base the planned meetings are created on |
| `MAILROOM_API_KEY` | — | the Vexa API key presented as `X-API-Key` (required unless `MAILROOM_DRY_RUN`) |
| `MAILROOM_WORKSPACE_MAP` | — | `address=workspace_id` pairs, comma-separated. **This map is the workspace resolution.** |
| `MAILROOM_WORKSPACE_ADDRESS` / `MAILROOM_WORKSPACE_ID` | — | single-pair shorthand for the dev deployment |
| `MAILROOM_STATE_PATH` | `/data/mailroom-state.json` | bindings + cursor + notices (one atomic JSON file) |
| `MAILROOM_POLL_INTERVAL_S` | `30` | how often the loop reads the mailbox |
| `MAILROOM_BATCH_LIMIT` | `50` | messages per poll |
| `MAILROOM_AUTO_JOIN` | `true` | whether planned meetings arm the bot |
| `MAILROOM_DRY_RUN` | `false` | decide everything, mutate nothing (the first live smoke) |
| `MAILROOM_INTERNAL_SECRET` | — | when set, `/internal/*` requires `X-Internal-Secret` |
| `PORT` / `HOST` / `LOG_LEVEL` | `8030` / `0.0.0.0` / `info` | the liveness app |

A mailroom with no key or no workspace map **still boots** and reports
`ingest.configured=false` on `/health` — the alternative hides the reason inside a restart counter.

## Running it against Mailpit

```bash
# 1. a mailbox (dev only — Mailpit is an SMTP sink with an HTTP API)
docker run -d --name mailpit -p 1025:1025 -p 8025:8025 axllent/mailpit

# 2. the mailroom, pointed at it and at a running gateway
export MAILPIT_URL=http://localhost:8025
export MEETING_API_URL=http://localhost:18056          # compose API_GATEWAY_HOST_PORT
export MAILROOM_API_KEY=<a Vexa API key>
export MAILROOM_WORKSPACE_MAP="mk-dev@dev.vexa.ai=<workspace id>"
export MAILROOM_STATE_PATH=/tmp/mailroom-state.json
export MAILROOM_DRY_RUN=1                              # drop this to actually plan meetings
cd core/meetings/services/mailroom && uv run python -m vexa_mailroom

# 3. send it an invitation (any corpus fixture, or a real one from your calendar)
python3 - <<'PY'
import smtplib, pathlib
from email.message import EmailMessage
ics = pathlib.Path("tests/fixtures/ics/oracle/gcal-create-single.ics").read_text()
m = EmailMessage()
m["From"], m["To"], m["Subject"] = "organizer@example.com", "mk-dev@dev.vexa.ai", "Invitation"
m.set_content("invite"); m.add_alternative(ics, subtype="calendar", params={"method": "REQUEST"})
smtplib.SMTP("localhost", 1025).send_message(m)
PY

# 4. watch what it decided
curl -s localhost:8030/internal/poll -X POST | jq .counts
curl -s localhost:8030/internal/bindings | jq '.bindings[] | {uid, meeting_id, scheduled_at}'
curl -s localhost:8030/internal/notices  | jq '.notices[] | {reason, to}'
```

To point the tests at a regenerated (date-shifted) corpus instead of the vendored one:
`MAILROOM_ICS_CORPUS=/path/to/ics uv run pytest -q tests/test_corpus.py`.

## Isolated evaluation

```bash
uv run pytest -q        # uv manages this package's own venv/deps
```

Autonomous: no docker, no network, no Mailpit, no gateway. `tests/conftest.py` injects a fake
mailbox and a fake control plane behind the ports, so every test drives shipped code and records
exactly what the mailroom asked the control plane to do. Levels: **L1** parser + config · **L2**
the 22-fixture invitation corpus (Google + Exchange, replayed in order) · **L3** the two adapters
against `httpx.MockTransport`, and the loop's properties (idempotency, resume, fail-safe).

## Status

- ✅ delivered — invitation ingestion behind a `MailSource` port; Mailpit adapter (dev)
- ✅ delivered — Google + Exchange invitation parsing: `METHOD:REQUEST`/`CANCEL`, Windows `TZID`,
  `RRULE` series, `SEQUENCE` updates, `EXDATE`, link recovery from conference property /
  `LOCATION` / folded `DESCRIPTION`, roster with `ROLE`/`PARTSTAT`
- ✅ delivered — planned meetings on the public API; series binding, re-schedule, cancel
- ✅ delivered — idempotency (UID+`SEQUENCE`), resume-safe cursor, notice log, 22/22 corpus
- ⬜ not done — a real mailbox transport (IMAP / inbound SMTP); Mailpit is dev-only
- ⬜ not done — outbound notices. Notices are recorded and addressed, never sent (Stage 1 + SMTP)
- ⬜ not done — participants on the meeting record. The invitation's roster is captured in the
  binding, but the control plane has no participant surface to carry it yet
- ⬜ not done — multi-workspace provisioning. The map is a config value; there is no admin flow
- ⬜ not deployed — helm/hosted. Compose-only (`--profile mailroom`)

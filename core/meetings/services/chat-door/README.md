# core/meetings/services/chat-door — the door (dev v0)

## Purpose
An artifact email reaches a **participant**, not a user: someone with no account, no API key
and no reason to visit a dashboard. This service is the one door that costs them nothing to
open — the record link inside their artifact email is a **signed, expiring, single-use magic
link**, and one click puts them on their meeting's record with a box that steers their next
artifact. The person becomes a user *at that click* and not before.

It ships with its other half: the **postman**, a CLI that turns a rendered artifact file into
the email carrying that link.

> **This is v0 and the pages say so.** There is no chat model behind the reply box — the reply
> is appended to the person's personal-instructions document, which is the steering write path
> the product actually needs first. Chat comes after.

## Seams
| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| consumes | the meeting API (through the gateway) | HTTP `GET /meetings/{id}` + `GET /meetings/{id}/transcript`, falling back to `GET /transcripts/by-id/{id}` | the meeting record the door renders — **as a client**, never an import |
| consumes | a rendered artifact file | filesystem | the markdown the postman mails |
| produces | any SMTP relay (dev: Mailpit) | SMTP | one `multipart/alternative` message per participant |
| produces | the personal context layer | `<store>/users/<slug>/personal-instructions.md` | one dated entry per steer |

No database, no queue, no imports across a domain boundary.

## The four routes
| Route | What |
|---|---|
| `GET /health` | liveness + the signing-key **fingerprint** (never the key) |
| `GET /door/verify?t=…` | verify the link (single-use) → lazily create the identity → set the session cookie → redirect to the record |
| `GET /door/meeting/{id}` | the record + the reply box, scope-checked |
| `POST /door/steer` | append a dated entry to the person's personal-instructions doc |

## Configuration
All env, all optional in dev. **`CHAT_DOOR_SIGNING_KEY` is the one secret** — with it unset a
key is generated per process, which the startup line states, and links then die on restart.

| Var | Default | Meaning |
|---|---|---|
| `CHAT_DOOR_SIGNING_KEY` | *generated* | HMAC key for links + sessions — the postman and the door must hold the same one |
| `CHAT_DOOR_BASE_URL` | `http://localhost:8080` | public origin used to build links |
| `CHAT_DOOR_MEETINGS_URL` | `http://gateway:8000` | the meeting API |
| `CHAT_DOOR_MEETINGS_API_KEY` | *unset* | `X-API-Key` forwarded to it |
| `CHAT_DOOR_STORE_DIR` | `./.chat-door-store` | user rows + personal docs |
| `CHAT_DOOR_RECORDS_DIR` | *unset* | **dev-only** — read records from corpus JSON on disk instead of the API; every page served this way says so |
| `CHAT_DOOR_LINK_TTL_SECONDS` | `604800` | magic-link lifetime |
| `CHAT_DOOR_SESSION_TTL_SECONDS` | `86400` | session-cookie lifetime |

The key is held in a wrapper whose `repr`/`str` yield `sha256(key)[:8]`, so a traceback, a
config dump or a validation error cannot leak it. `/health` reports that fingerprint, which is
how two processes confirm they agree on a key without either emitting it.

## Demo (the whole loop, no Docker on the laptop)
```bash
cd core/meetings/services/chat-door
export CHAT_DOOR_SIGNING_KEY='<a dev key you choose>'   # same value in both shells
export CHAT_DOOR_BASE_URL=http://127.0.0.1:8087

# 1 · the door, reading records from a local corpus (dev source)
PYTHONPATH=src CHAT_DOOR_PORT=8087 CHAT_DOOR_HOST=127.0.0.1 \
  CHAT_DOOR_RECORDS_DIR=/path/to/artifact-loop/corpus \
  CHAT_DOOR_STORE_DIR=/tmp/door-store \
  uv run --with uvicorn python -m chat_door

# 2 · mail one rendered artifact (Mailpit: ssh -fNL 11025:127.0.0.1:11025 <dev-host>)
PYTHONPATH=src uv run python -m chat_door.postman \
  --artifact /path/to/artifact-loop/rendered/124/dmitry-grankin.md \
  --to you@example.test --smtp-host 127.0.0.1 --smtp-port 11025

# 3 · open the mail in Mailpit, click the record link → the record + the reply box.
#     Post a steer, then read what it wrote:
cat /tmp/door-store/users/*/personal-instructions.md
```
`--dry-run out.eml` writes the message instead of sending it. The magic link is **not** echoed
unless you pass `--print-link` — it is a bearer capability for one person's record.

## Isolated evaluation
```bash
uv run pytest -q      # 53 tests, no docker, no network
```
Covers: token issue/verify/expiry/single-use/tamper/wrong-key · lazy creation on the first
successful click only (and nothing on a failed one) · scope refusal across meetings ·
transcript-route fallback and the empty-vs-unreadable distinction · artifact parsing in two
languages · MIME shape and the embedded link · a real SMTP send against an in-process stub
server (`tests/smtp_stub.py` — Python removed `smtpd` in 3.12 and docker does not run here) ·
the postman→door round trip, including the mismatched-key failure.

## Status
- ✅ magic links: signed · expiring · single-use · fail-closed with stable reasons
- ✅ lazy identity + the personal-instructions document, written by the reply box
- ✅ the postman: rendered artifact → `multipart/alternative` → SMTP, link embedded
- ✅ scope: a session reads the one meeting its token named; `guest` never reaches group context
- ⬜ **no LLM chat** — the reply box stores, it does not converse
- ⬜ **single workspace** — no membership table; scope is asserted by the link issuer
- ⬜ **dev-only** — file store, process-local single-use ledger, no revocation-on-removal
- ⬜ group proposal queue (owner triage) — the personal layer lands directly; the group path is not built

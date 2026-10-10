# transcript.v1 — speaker-attributed segments + session envelopes

The product's core output, and the **TS↔Py seam**: the bot (TS) produces it; the collector (Py)
persists it; the gateway forwards the live bundle to the dashboard. Both languages validate against
this schema (P4).

## Shapes (`$defs`)
- **`TranscriptSegment`** — `segment_id · speaker · text · start/end (sec)` + optional `language ·
  completed · absolute_* · source` (attribution) · `confidence` · `words`.
- **Bus stream** (bot → collector): `SessionStart` → `Transcription` (confirmed batches) → `SessionEnd`.
- **`MutableBundle`** — the live `confirmed`+`pending` bundle the gateway forwards verbatim to the dashboard.

## The bus entry is signed (`StreamEntry`)
Every bot appends to the one `transcription_segments` stream, so each entry carries, beside `payload`
(the JSON of one bus message), proof that its session may speak for the meeting the payload names:

| Field | Value |
|---|---|
| `payload` | the bus message, as a JSON string |
| `auth` | the `header.payload` part of the session's MeetingToken (invocation.v1 `token`); its claims name the meeting |
| `sig` | HMAC-SHA256 of the `payload` string, keyed with the whole token, as 64 lowercase hex digits |

The collector rebuilds the token from `auth` with the secret that minted it, so the bearer never enters
Redis. It admits an entry only when the token is valid, the signature holds and the payload's
`meeting_id` is the token's; anything else is acknowledged and dropped (logged
`segment_entry_refused`). **A bot older than v0.13.2 writes no `auth`/`sig`, so its entries are
dropped**: run bots and meeting-api from the same release. A tool that publishes to the stream signs
the same way, with [`segment_entry.py`](segment_entry.py) (vendored byte for byte into meeting-api and
the compose stack test, fact `segment-entry-signer`); the bot's TypeScript twin is
`transcript-redis.ts` `entryAuth`. `SignedEntryVector.meeting-42.json` pins both, and `validate.mjs`
re-signs it in Node (fact `segment-entry-vector` holds the same signature in both services' tests).

## The per-meeting feed (`FeedEntry`)
`tc:meeting:{meeting_id}` — keyed by the meetings-domain numeric **row id**, never the native id, which
collides across users and rows — is the collector's, and it writes there **only entries it admitted**
from the bus. Each entry is `{payload}`, the JSON of one of:

- **`FeedTranscription`** — one persisted, non-empty segment (`FeedSegment`); `session_uid` and
  `meeting_id` carry the native id for display;
- **`FeedRetract`** — `segment_ids` the bot superseded;
- **`FeedSessionStart`** — `{type: "session_start", session_uid}`: the meeting is live (again) on
  this row; meeting-api writes it when the lifecycle reaches `active`, so a reused row that ended
  before is not read as ended;
- **`SessionEnd`** — `{type: "session_end", session_uid}`: the meeting is over.

agent-api's transcription watcher registers and ends meetings from this feed, and the terminal's live
view reads it through agent-api, so a reader may act on what it finds here without re-checking it.

## Deliberately **not** in this contract
- **The payloads carry no credential.** The bus message (`payload`) names the meeting and nothing
  more; the session's proof is beside it in the entry (`auth`, `sig`). A contract describes the data
  and the proof, never a bearer (ADR-0001).
- **tenant / owner / visibility are deferred** (ADR-0003). They are added *additively* to `SessionStart`
  when sharing/multitenancy is built — optional fields, back-compatible, no refactor. The seam is the
  `canAccess` port, built then.

## Conformance
Goldens in [`golden/`](golden/) named `<Shape>.<case>.json`; `validate.mjs` (ajv) validates each against
its `$def` (the filename prefix). Run by `gate:schema`.

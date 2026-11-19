# signals — transcript signals

A small, company-chosen model listens to live sources — a meeting's verified transcript, and a room's
messages (a chat with two or more people, with a free rule tier in front of the model) — against
the **listening brief** the meeting's agent wrote, and wakes that agent through the company's own flow
when what it heard adds up. Off until `_global/POLICIES.md` says `transcript_signals: on` and names a
`model_signals`. Operator and user guide: [`docs/docs/transcript-signals.mdx`](../../docs/docs/transcript-signals.mdx).

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| reads | redis `transcription_segments` | consumer group `signals` | hints only: which meeting row to read, and its owner (token claims, unverified) |
| reads | redis `tc:meeting:{row}` (meetings is the only writer) | consumer group `signals` | confirmed lines from the saved position |
| reads | redis `room:{session}:msgs` (agent-api is the only writer) | consumer group `signals` | a room's messages; the agent's own are context only |
| reads | agent-api | `GET /internal/meetings/{row}/listen?subject=`, `GET /internal/chats/{session}/listen?subject=` (internal secret) | the brief and the company's signal policy |
| reads | credential broker | `credential-broker.v1`, role `signals` | the signal model's key (`service/signals/<name>`) |
| calls | the signal model | `jev` or `openai-compatible` adapter | one line + 24 lines of context + the brief's questions → a 0..1 score per label |
| writes | redis `sig:meeting:{row}` and `sig:state|seen|lease|live:*` | only signals | annotations; its own memory, under its lease |
| publishes | flows-api | `POST /events/signal` (internal secret, `X-Vexa-Service: signals`) | event.v1 `meeting.signal`, `data` = the buffer |

signals never wakes the agent directly and never writes the brief: the company's `_global` flow decides
what a wake does, and agent-api is the brief's only writer.

## Contracts

**Owns:** [`contracts/signals.v1`](contracts/signals.v1). **Consumes:** `transcript.v1` (the feed),
`credential-broker.v1` (its assertion is vendored as `src/meeting_signals/broker_assertion.py`),
`event.v1` (the wake), `config.v1` (`src/meeting_signals/config.v1.json`, preflight vendored).

## Run and test

```sh
uv run pytest -q                                   # no network, no containers
PYTHONPATH=src uv run python -m meeting_signals.replay transcript.json --model fake
```

`replay` runs the same listener over a transcript JSON on a simulated clock and prints each wake's
time, reason, line count and score — never the text unless `--show-text` is given.

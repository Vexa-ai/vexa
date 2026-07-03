# vexa-slim — minimal gateway-only client (SoC validator)

A single-file Python client that exercises the meeting/agent control plane **through the gateway only**
(`api.v1`). It holds no redis URL and no domain internals — so if it can do a job, the `meetings ⊥ agent`
boundary holds. It's both a fast scaffold and a living proof of separation.

## Levels (highest → lowest)

- **`vexa_slim/cookbook.py`** — the top of the library: high-level composed operations that return data.
  `listen_to_meeting`, `agent_on_meeting`, `harvest`. Import and call them; they orchestrate the client.
- **`vexa_slim/scenarios.py`** — the programmatic validator (`run_processor`): the happy path with a
  pass/fail verdict + exit codes, driven by the CLI.
- **`vexa_slim/client.py`** — the SDK: two peer sub-clients, `slim.agent.*` and `slim.meetings.*`.

```python
from vexa_slim import Slim, listen_to_meeting
from vexa_slim.config import api_key, gateway_url, load_env

load_env()
slim = Slim(gateway_url(), api_key())
harvest, doc = await listen_to_meeting(slim, "ety-jhht-nek", seconds=20)
print(harvest.counts())          # e.g. {"transcript": 80, "note": 99, "card": 33}
# the kinds ("note", "card", …) are defined by the agent WORKSPACE TEMPLATE, not the client.
```

## First scope: the meeting-processor agent

`run` validates the listening/processing agent end-to-end: turn the copilot processor ON, watch the merged
live feed, and assert the agent emits cleaned **notes + cards** — not just raw transcript.

## Setup

```bash
pip install httpx
```

Config is read from the environment, falling back to `clients/terminal/.env.local` so it works against a
local stack out of the box:

- `GATEWAY_URL` (default `http://127.0.0.1:18056`)
- `VEXA_API_KEY` or `VEXA_BOT_API_KEY` — the gateway resolves this to a user and injects `X-User-Id`.

## Commands

```bash
# auth + agent-api reachability smoke
python -m vexa_slim models

# FULL scenario — needs a transcript flowing (a bot already in the meeting),
# or pass --send-bot to put one there first:
python -m vexa_slim run abc-defg-hij --seconds 45
python -m vexa_slim run abc-defg-hij --send-bot https://meet.google.com/abc-defg-hij

# individual jobs
python -m vexa_slim process abc-defg-hij          # processor ON
python -m vexa_slim process abc-defg-hij --off     # processor OFF
python -m vexa_slim watch   abc-defg-hij --seconds 30
python -m vexa_slim doc     abc-defg-hij            # the agent's durable meeting doc
python -m vexa_slim send-bot abc-defg-hij --url https://meet.google.com/abc-defg-hij
python -m vexa_slim stop-bot abc-defg-hij
```

## Verdict (exit codes)

`run` prints a verdict and exits:

- `0` PASS — transcript flowed **and** the processor emitted notes/cards.
- `1` FAIL — transcript flowed but 0 notes/cards (agent not producing), or `model-error` events (LLM/provider).
- `2` setup error (no API key / agent-api unreachable).
- `3` INCONCLUSIVE — no transcript flowed (bot not in the meeting / wrong `native_id`).

## What it maps (the full terminal job set, for later waves)

- **meetings:** `send/stop bot`, `intent`, `list`, `transcript history`, `WS status` — via gateway → meeting-api
- **agent:** `process` (processor toggle), `chat`, `meeting doc`, `workspace tree/file`, `models` — via gateway → agent-api
- **cookbook:** `agent-on-meeting` (bot + process), `chat grounded in meeting` (meeting-scoped tool)

This first cut implements the agent-processor slice; the rest land as additional subcommands.

# scripts/probe — the standing full-journey smoke probe (`make probe`)

The hot-loop entry point an agent or operator drops a debugging hypothesis into: ONE
journey driven entirely through a surface's gateway front door — **spawn → schedule →
boot → join → transcribe → live-view → stop** — followed by a one-shot all-component
log sweep (the parallel failure inventory). Each stage prints Expected / Actual /
Verdict; any red fails the command (exit 1) after the sweep still runs. No real
meeting, no audio, no human: minutes to a truthful verdict.

## Entry points

```bash
make probe                    # compose (the fast default)
make probe SURFACE=lite
make probe SURFACE=helm
```

The root `Makefile` delegates to `deploy/<surface>/probe.sh`, exactly as `make all` /
`make lite` delegate bring-up. Each wrapper resolves its surface's gateway URL, mints
a `bot,tx` API key (compose: `bin/provision-token` · lite: the in-container admin-api
· helm: the release secret + `kubectl port-forward`), defines the surface's log-sweep
command, and runs the ONE shared journey here.

## Files

| File | Role |
|---|---|
| `journey.sh` | the shared journey: stages S0–S8, the one contract all surfaces fan into |
| `ws_tail.py` | live `/ws` feed listener started BEFORE spawn (reuses `deploy/compose/tests/_ws.py` — no new WS client) |

## Modes

- **mock** (`BROWSER_IMAGE` = `mock-bot:dev`, compose) — `bot_name=mock:normal` +
  `mock:immediate-stop`: a deterministic green full journey, transcript segments and
  the DELETE-driven stop included.
- **real** (default) — the real bot at a dead synthetic meeting URL: the truthful
  terminal is a NAMED failure (`join_failure`), never a fake green. A stack whose
  spawn is broken goes red at **S3 boot** (the row never leaves `requested`).

Wiring is pinned offline by `deploy/compose/tests/probe_wiring_test.py`.

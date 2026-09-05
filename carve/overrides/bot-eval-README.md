# @vexa/bot — eval harness

Validates the standalone bot service against a live meeting: it spawns the bot, bridges its
`lifecycle.v1` and `transcript.v1` streams to a local viewer, drives synthetic speakers, scores the
resulting transcript against a baseline, and prints a `VERDICT PASS|FAIL`.

One command in, one verdict out — the bot is treated as a module whose only distinguishing
property is that it is a runnable service.

## Running

```bash
make -C core/meetings/services/bot/eval run MEETING=<meeting-code>
```

Copy `config.env.example` and fill in your own endpoints and credentials first. The viewer serves
on `localhost:8090`; you will need to admit the bot to the meeting once it joins.

## Pieces

| File | Role |
|---|---|
| `Makefile` | entry point — `run`, plus the sub-targets below |
| `run.sh` | spawns the bot and wires its event streams to the viewer |
| `feed.mjs` | drives synthetic speakers into the meeting |
| `attribute.mjs` | maps transcript segments to expected speakers |
| `verdict.mjs` | scores the run and emits the PASS/FAIL verdict |
| `verify.sh` | preflight checks before a run |
| `viewer/` | live transcript viewer (static page + small server) |
| `config.env.example` | template for endpoints and credentials |
| `O6-STANDALONE.md` | notes on running the bot standalone |

This harness reuses the shared machinery under `core/meetings/eval` rather than forking it, and
adds only the bot-targeted runner, the viewer, and the verdict/attribution glue.

# O6 Meet-leg — standalone bot validation (automated)

Supersedes the manual ssh recipe in `meetings/eval/O6-MEET-LEG.md`. That doc's steps (spawn the carved
bot on bbb · admit · drive speakers · `redis-cli XRANGE … | read-redis-transcript | analyze`) are now a
**single command** with a live viewer and one autonomous verdict:

```bash
make -C meetings/services/bot/eval run MEETING=rvf-kywf-pxb
```

The harness automates every step of O6-MEET-LEG and adds: the **eyeball viewer** (live transcript +
lifecycle + verdict banner), the **autonomous verdict** vs `BASELINE.md`, and **module attribution +
offline-replay** on red. The bot side (join · admit · capture · redis egress · scoring) is the proven
O6 path; only the audio→STT leg needs real audible speech (driven speaker-bots, prod tokens).

## What it proves
Same as O6-MEET-LEG, but repeatably and with one verdict: the carved `vexaai/vexa-bot:v012` joins a
live Meet on bbb, captures the synthetic speakers, publishes `transcript.v1` to redis, and the
transcript scores within the `BASELINE.md` gmeet bar (`misattr=0`, `seg_N=0`, low oversegmentation,
`leakage=0`).

## Evidence log
_Record each green/red run here (date · meeting · the VERDICT line · notes). The first automated run is
the acceptance test for this harness._

| Date | Meeting | VERDICT | Notes |
|---|---|---|---|
| _pending_ | `rvf-kywf-pxb` | _to be recorded_ | first automated standalone run (replaces the manual O6-MEET-LEG steps) |

## Relationship to the offline gate
A red verdict's flagged signal reproduces OFFLINE + deterministically through the REAL gmeet pipeline
via `pnpm --filter @vexa/bot run replay` (`gate:replay`, `../src/replay.test.ts`) — the live→offline
loop that lets the owning brick be fixed without a flaky live Meet.

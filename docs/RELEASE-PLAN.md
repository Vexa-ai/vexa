# RELEASE-PLAN — the full path to 0.12

The **always-current plan** (ADR-0015): the staged path from where we are to the **0.12 OSS release**.
Keep this current — it is the macro expectation the expectation–reality loop (§8) executes against.
Detailed rules live in [`ARCHITECTURE.md`](ARCHITECTURE.md); decisions in [`adr/`](adr/).

_Last updated: 2026-06-20._

## Goal (the destination — end of plan)
A self-hostable **0.12** where every component is validated **in isolation (L1/L2) and integrated (L3/L4)**.
**Release spine (order):** desktop+extension → standalone bot → meeting-api → dashboard → agents → deploy → release.

## Critical path & parallelism
- **Critical path:** bot (P2) → meeting-api (P3) → dashboard (P4) → deploy (P6) → release.
- **Independent roots (parallel):** desktop+extension (P1), agents (P5), identity/gateway. All build behind the **sealed contracts** (`runtime/transcript/lifecycle/acts/invocation/recording/workspace .v1`).

## Status — done · in-flight · remaining

| Phase | State | Notes |
|---|---|---|
| **P0** process + gates | ✅ done | remote+CI green; `gate:contract-version`·`fault-surfacing`·`client-liveness` **have**; contracts frozen |
| **Constitution growth** | ✅ done | **P18** fail-loud · **P19** prove-at-altitude · **P20** complete-mediation · **P21** state-from-evidence; process: **ADR-0014** (expectation–reality loop) · **ADR-0015** (plan/exec modes) |
| **P1** desktop + extension | ✅ **L4 PASSED** | endpoints + recording playback + mic-CSP + capture tests + P21 evidence-driven state; **both lanes validated live** (mixed + gmeet, misattr=0), baseline banked (`eval/BASELINE.md`) |
| **P2** bot | 🟡 core + 2a transports done · **2b browser next** | composition root + ports + orchestrator + 42 L2 (3 review bugs fixed); **2a = live redis/HTTP transports** (lifecycle·transcript·acts, L3). **2b = browser** (join via @vexa/join+remote-browser, capture→pipeline, recording upload — L4) |
| **P5** agents | 🟡 skeleton done | Python agent-api, transcript.v1 seam, 15 pytest (ADR-0009). LLM loop + real adapters remain |
| **P3** meeting-api + identity/gateway | ⬜ not started | cloud receiver (recording.v1/lifecycle.v1 + REST/WS + Postgres); the eval 4-op API |
| **P4** dashboard | ⬜ not started | vendored Next.js → wire to live API; security debt (JWT default-secret) |
| **P6** deploy | ⬜ not started | lite · compose · helm, validated |
| **Release** | ⬜ | full-system L4 ≥ baseline |

## Current objective
**Validate the P1 capture lanes live + capture the 0.12 eval baseline.** STT is unblocked (internal token from vexa-secrets); the desktop is up with tape-recording. Instrument-validated already: STT+pipeline (replay→13 segs), capture-liveness (25 L2), gates green. **Open (human-required, minimal surface):** reload the rebuilt extension → YouTube (mint stream) + Meet (2nd speaker); the tape `capture`/`analyze` instruments adjudicate (§8 — cross-validate the human).

## Gate debts (make the new principles bite — close before release)
- ~~`capture.v1` sealed~~ ✅ **done** — 7 golden vectors + round-trip conformance (`capture-v1-golden.test.ts`, 21 assertions, under `gate:node`); spec `capture-v1.md` (P4 refinement, the busiest wire).
- ~~`gate:eval-baseline`~~ ✅ **done** — `meetings/eval/BASELINE.md`: both lanes L4-passed live (mixed `youtube` 130 segs · gmeet `dps-nwbw-jzz` 13 segs, **misattr=0** both); gmeet per-participant `ch0` exercised via a shared presentation. **The P1 L4 milestone.**
- ~~`gate:access` + `canAccess`~~ ✅ **done** (desktop) — port on `/transcripts`·`/recordings`·`/player`·`/bots`·`/ws`; `access.test.ts` (13 L3, deny⇒403/empty). *(seam only; default allows-all on localhost; real grants + the cloud meeting-api inherit it — ADR-0003.)*
- ~~`gate:health`~~ ✅ **done** — desktop `/health` (STT readiness) + no-frames watchdog (P18 server-side); `health.test.ts` (10 L3 checks). *(follow-on: a live STT reachability ping; today /health reports STT *configured*, not pinged.)*

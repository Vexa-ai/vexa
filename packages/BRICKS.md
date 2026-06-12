# packages/ — bricks (modules)

Modules are units of codebase — the only place code lives (MANIFEST P1).
Extracted bricks land here, one directory per module, joining the existing
inhabitants (`transcript-rendering`, `vexa-client`, `vexa-cli`).

Every brick ships the same nine artifacts (MANIFEST §3b):

| Artifact | Rule |
|---|---|
| `README.md` | what it does, contract summary, run watch/replay — 5 lines |
| `docs/` | updated in the SAME PR as any behavior change (`gate:docs`) |
| `contract/` | re-exports from `/contracts` — the only public surface |
| `src/` | internals; free to change, invisible outside |
| `harness/driver/` | feeds fixtures or synthetic input through contract-in |
| `harness/oracle/` | asserts contract-out |
| `harness/lens.md` | how to watch: VNC port / trace endpoint / diff command |
| `fixtures/` | small inline; large by content hash → store |
| `check-isolation` + `Makefile` (`watch·replay·record·gates`) + `Dockerfile` + lockfile | standalone build; isolation fails on any boundary escape |

One-way import rule: bricks import contracts, other bricks' published
artifacts, and third-party deps — never `services/`, never `libs/`, never
another brick's `src/`. Services import bricks freely: that is the thinning.

Extraction order (MANIFEST §7): `meet-join` (MVP1) → capture-kit,
speaker-streams, audio-pipelines, diarization, delivery (MVP2) → infra bricks
as services thin (MVP3–4).

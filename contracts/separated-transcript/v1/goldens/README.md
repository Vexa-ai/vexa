# separated-transcript.v1 goldens

Recorded at MVP2 from `production-replay` (FULL mode), one golden per topology —
each is the exact `SeparatedSegment[]` the pipeline emits for a known fixture,
and the spec the attribution oracle diffs against:

| Golden | Topology | Source fixture |
|---|---|---|
| `per-participant-gmeet-2026-NN-NN.json` | `per-participant` | gmeet multistream capture (channel = identity) |
| `mixed-msteams-2026-NN-NN.json` | `mixed` | msteams mixed capture (diarization cluster ids) |

Until recorded, the schema in `../schema.ts` is the only spec; these land in the
same PR that promotes the pipeline bricks' `make replay` gate (MVP2).

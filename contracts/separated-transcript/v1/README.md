# separated-transcript.v1 — speaker-separated transcript segments (to formalize at MVP2)

The intermediate boundary the pipeline spine grew but never named. Producers:
the two transcription-pipeline bricks — **mixed-pipeline** (zoom/msteams, mixed
audio → diarizer) and **multistream-pipeline** (gmeet, per-channel audio →
channel labels). Consumer: **speaker-attribution**.

**The opaque-key rule (the whole point of the seam):** a segment is labelled by
`speakerKey` — a capture channel id (`"speaker-3"`, multistream) or a diarization
cluster id (`"spk_0"`, mixed) — **never a resolved participant name**. Resolving
the key to a name is the next brick. This keeps both pipelines identity-free and
single-sources the attribution logic that today lives fused in
`speaker-streams/src/speaker-mapper.ts`.

Two producers, one contract: the strategies differ (diarizer vs channel-labeler)
but the contract-out is byte-identical, which is exactly why mixed and
multistream are **separate bricks** emitting **one** schema with **one** oracle.

Downstream: speaker-attribution consumes this + capture.v1 name events and emits
`transcript.v1` (final, named segments to the collector).

Goldens: recorded at MVP2 from `production-replay` (FULL mode) — one per topology
(`mixed-*`, `per-participant-*`). See `goldens/README.md`. The goldens are the spec.

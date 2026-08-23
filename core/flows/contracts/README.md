# contracts — the brick's three sealed boundaries

`event.v1` (envelope in: type · source_id · opaque refs · provenance — no behavior, no
capabilities), `step.v1` (refs + effect_key in → receipt out), `status.v1` (the projection the
terminal/operator reads). Schemas + goldens land here as the real adapters land; registration in
architecture.calm.json follows the repo's contract-version gate.

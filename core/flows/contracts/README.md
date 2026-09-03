# contracts — the brick's three sealed boundaries

`event.v1` (envelope in: type · source_id · opaque refs · provenance — no behavior, no
capabilities), `step.v1` (refs + effect_key in → receipt out), `status.v1` (the projection the
terminal/operator reads). Schemas + goldens land here as the real adapters land; registration in
architecture.calm.json follows the repo's contract-version gate.

`flows.v1` is here now, and it is a different kind of thing from the three above: not a wire shape
but the **carrier census** — every event type with exactly one producing domain, its owner, the refs
a consumer may rely on, and its cardinality. It is the registry `gate:config-contract` checks a
service's `publish-edge` keys against, which is what makes a publish edge declarable as the thing it
is rather than as a dependency. Registered in the chart, deliberately **unsealed** while in
development.

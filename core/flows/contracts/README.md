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

`event.v2` is the **sealed fact envelope** (PRD 0.13.4 D8): the producer computes who a fact reaches
(`audience`, `owner`, `visibility`, a card's lifecycle coordination fields) and seals it per
producer; `POST /events` verifies it and admission starts a person's flow when the person is the
subject or in the audience. Its `fact_envelope.py` is the canonical copy the crm outbox and the
flows intake vendor (parity fact `fact-envelope`). Unsealed while in development.

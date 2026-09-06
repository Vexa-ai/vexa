# flows.v1 goldens

Reference instances that pin the contract. Filename = `<Shape>.<case>.json`; the part before the
first dot is the `$def` (`Carrier`, `CarrierRegistry`) each must conform to. Validated by
`../validate.mjs` (gate:schema), alongside the live census at `../carriers.json`.

`Carrier.onboarding-completed.json` is the entry a consumer builds against: the exact refs identity
promises, the stamp behind the exactly-once claim, and the id the fact is keyed by. Add a golden for
every new case the shape must keep accepting.

`Carrier.friction-reported.json` is a `once_per_occurrence` carrier with no `stamp` (the schema
requires one only when `cardinality` is `exactly_once_per_subject`) — the case a reader needs
pinned is that recurrence is the point, so nothing here claims a durable dedup guarantee it does
not hold.

**Seven goldens for seven carriers, and the last three were the gap.** `gate:schema` validates the
live census as one document plus whatever is in this folder, so four goldens over seven registered
carriers meant three shapes were pinned only by the census — and a census entry is the thing under
change, not the reference the change is checked against (P8: the L1–L2 spec was red for exactly
this). The three that were missing each carry a distinct case:

| golden | the case it pins |
|---|---|
| `Carrier.invite-received.json` | the minimum a Carrier may be — no `source_event_id`, no `stamp`, a single ref, and `flows` as its own producer |
| `Carrier.desk-unscaffolded.json` | `exactly_once_per_subject` whose stamp is **not a column** (the desk on disk), and the first `published_by: "private"` entry |
| `Carrier.claim-proposed.json` | a two-ref `once_per_occurrence` carrier that is `private` and correctly carries no `stamp` |

A golden and its census entry must agree on the CONTRACT — owner, cardinality, refs, whether a
stamp stands behind an exactly-once claim, and `published_by` — and `tests/test_carrier_census.py`
checks exactly that, per carrier. Their prose may differ and in two cases does: the meeting
goldens carry the empty-room race on the calendar-intake path written out in full, where the
census summarises it. That is a reference instance being more useful than the registry, not drift.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

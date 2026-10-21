# event.v2 goldens

Filename = `<Shape>.<case>.json`, validated against `#/$defs/<Shape>` by `../validate.mjs`. A case
starting `reject-` must FAIL to validate — the refusals are part of the contract.

Every envelope here is sealed with the test secret `golden-seal-secret` under the `crm` producer key,
at `now` = 1792310400 (so `exp` = 1792310700), over its own `Event`'s fact (event type, event id,
refs); the `Envelope.reject-*` cases carry the card fact's seal. `core/flows/tests/test_fact_envelope.py`
re-verifies each seal with `fact_envelope.verify` at that `now`, so a change to the canonical form, the
fact binding or the key derivation breaks these files rather than passing silently.

| golden | the case it pins |
|---|---|
| `Event.card-fact.json` | a card decision: subject = the actor, audience = actor, next actor, `ws:` principal; lifecycle fields only |
| `Event.record-fact.json` | a record fact: no lifecycle |
| `Event.legacy-no-envelope.json` | a row written before D8 — still a valid fact, admitted by subject only |
| `Envelope.reject-content-field.json` | a `title` beside the envelope keys (C8, M5) |
| `Envelope.reject-lifecycle-extra-key.json` | a `summary` inside `lifecycle` |
| `Event.reject-caller-claimed-audience.json` | `_audience` in a caller's `refs` |

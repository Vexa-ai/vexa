# golden vectors — unit.v1

Committed example vectors. Each `<Shape>.<case>.json` validates against `#/$defs/<Shape>` via `../validate.mjs`. The goldens ARE the spec (P8).

`InputVector.*` is a unit-input vector with a fixture secret: `validate.mjs` re-derives the unit key and
the signature in Node, and `core/agent/tests/test_unit_input.py` does it with `shared/unit_input.py`.
`InputEntry.signed.json` is the entry that vector produces.

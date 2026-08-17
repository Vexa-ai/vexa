# context_stack

Read [`layers.py`](layers.py) first — the four-layer table lives there once, and every other file
reads it rather than restating it. The parent [README](../../README.md) has the file-by-file map
and the three decisions behind the shape.

Two boundaries inside this package are load-bearing and both are asserted by tests:

- `router.py` (the machine path) cannot reach `triage.py` (the human path). The dependency runs
  the other way, so importing it back is a cycle Python refuses to load.
- `material.py` — the one reader of secret material — is imported by neither `secrets.py` nor
  `api.py`, which is what makes "no endpoint returns a secret" a fact about the import graph.

# identity/tests — identity-core evals (ride gate:python)

Pure unit evals, no docker / no live stack:

- **`test_access.py`** — the `gate:access` deny-tests (riding gate:python until the orchestrator
  wires the named gate): `canAccess(otherUser, {meeting_transcript|recording|ws_subscribe}, read)`
  → DENY on each of the three read paths; owner → ALLOW; unowned + empty-subject → default-deny.
- **`test_tokens.py`** — in-scope valid, out-of-scope rejected, expired rejected, mint guards.
- **`test_secrets_broker.py`** — broker returns a scoped credential; the raw value never appears in
  repr/str/format, captured logs, or the audit trail.
- **`test_identity_contract.py`** — `identity.v1` goldens + the core's emitted shapes conform
  (jsonschema, mirroring the authoritative ajv2020 `validate.mjs`).

Run: `uv run pytest -q` (from `identity/`).

_Governed by `docs/ARCHITECTURE.md` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

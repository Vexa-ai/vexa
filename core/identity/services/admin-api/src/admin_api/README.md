# admin_api — identity service package

- `__main__.py` — the production entrypoint (`python -m admin_api`): boot preflight, DB engine from
  `DB_*`, schema convergence, the admin claim code at boot.
- `config.v1.json` + `config_preflight.py` — this service's deployment-config declaration and the
  vendored config.v1 boot preflight (canonical copy `deploy/contracts/config.v1/preflight.py`).
- `schema/` — the v0.12 SQLAlchemy source-of-truth + idempotent `ensure_schema()`.
- `app/` — the FastAPI surface (`create_app`) + injectable async DB wiring.
- `token_scope.py` — `vxa_<scope>_` token minting for {bot, tx, browser}.
- `delegation.py` — a worker's per-dispatch delegation token (`vxd_…`): mint and verify. Vendored
  byte for byte from the contract's canonical copy, `core/identity/contracts/delegation.v1/delegation.py`
  (fact `delegation-token`); `/internal/validate` verifies with it.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

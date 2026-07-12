# admin_api — identity service package

- `schema/` — the v0.12 SQLAlchemy source-of-truth + idempotent `ensure_schema()`.
- `app/` — the FastAPI surface (`create_app`) + injectable async DB wiring.
- `token_scope.py` — `vxa_<scope>_` token minting for {bot, tx, browser}.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

# src — the admin_api package

Importable source for the admin-api service + its schema. `pyproject.toml` puts `src/` on the
pytest pythonpath; the public surface is `admin_api` (`schema`, `app.main:create_app`,
`token_scope`).

_Governed by `docs/ARCHITECTURE.md` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

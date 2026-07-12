# identity/src — the identity domain CORE

`identity_core/` — the pure authN/authZ primitives the lane exports: scoped tokens (`tokens.py`),
the `canAccess` authz port + default-deny owner-only adapter (`access.py`), and the `SecretsPort`
credential broker (`secrets.py`). Dependency-light (stdlib + jsonschema in tests), no DB, no I/O —
distinct from `../services/admin-api/` (the runnable carve). Conforms to `../contracts/identity.v1`.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

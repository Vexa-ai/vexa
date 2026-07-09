# identity/services — runnable identity services

Long-running identity-domain services (each its own deployable). Today: `admin-api/` — users,
scoped API tokens, and the gateway's `/internal/validate` authz oracle.

_Governed by `docs/ARCHITECTURE.md` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

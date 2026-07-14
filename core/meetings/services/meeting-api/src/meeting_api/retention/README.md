# retention — raw meeting erasure core

Owner-scoped orchestration for deleting one meeting across Minutes-owned carriers. The core receives
an erasure plan from an injected repository, deletes recording objects first, commits transcript /
summary / meeting-row deletion second, and returns content-free counts.

This module owns no HTTP route, scheduler, database schema, storage client or policy default. Those
are adapters and composition concerns. Deleting objects before the database commit makes retries
safe: a storage failure leaves the database authoritative; a later database failure can retry over
already-absent object keys.

Public surface: `erase_meeting`, `ErasureReceipt`, `ErasureFailed`. Ports live in `ports.py`; offline
fakes in `fakes.py`; the focused two-tenant proof is `tests/test_zaki_retention.py`.

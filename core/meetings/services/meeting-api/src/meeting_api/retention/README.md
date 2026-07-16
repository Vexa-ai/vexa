# retention — raw meeting erasure core

Owner-scoped orchestration for deleting one meeting across Minutes-owned carriers. The repository's
`begin_erasure` boundary atomically blocks new recording writes and drains in-flight writes before it
returns a stable plan. The core then deletes every object under validated recording prefixes, commits
transcript / summary / meeting-row deletion second, and returns content-free counts.

This module owns no HTTP route, scheduler, database schema, storage client or policy default. Those
are adapters and composition concerns. Every recording writer must share the `MeetingWriteGate` with
the erasure adapter; an implementation that checks state without holding a lease is invalid. Deleting
objects before the database commit makes retries safe: a storage failure leaves the database in the
non-writable erasing state; a later database failure can retry over an already-empty prefix.

Public surface: `erase_meeting`, `ErasureReceipt`, `ErasureFailed`. Ports live in `ports.py`; offline
fakes in `fakes.py`; the focused two-tenant proof is `tests/test_zaki_retention.py`.

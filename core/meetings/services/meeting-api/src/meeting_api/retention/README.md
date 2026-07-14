# retention — raw meeting erasure core

Owner-scoped orchestration for deleting one meeting across Minutes-owned carriers. The repository's
`begin_erasure` boundary atomically blocks new recording writes and drains in-flight writes before it
returns a stable plan. The core then deletes every object under validated recording prefixes, commits
transcript / summary / meeting-row deletion second, and returns content-free counts.

This module owns no HTTP route, scheduler, database schema or policy default. `adapters.py` supplies
the production PostgreSQL repository and S3/MinIO prefix adapter. Every recording writer shares the
same PostgreSQL advisory-lock namespace with erasure: chunk upload and master finalization hold a
shared session lock across object + JSONB mutation; `begin_erasure` waits on the exclusive
transaction lock, persists the non-writable state, then releases it. A state-only check without the
lease is invalid.

Before the first object deletion, the core paginates a prefix census and persists the count in the
durable erasure metadata. Deletion repeats bounded 1,000-object batches until the prefix is empty and
the core verifies zero current-object residue. Deleting objects before the database commit keeps
retries safe: a storage failure leaves the database in the non-writable erasing state; a later
database failure retries over an already-empty prefix with the original census count. On an S3
bucket with versioning enabled or suspended, the census and delete cover every object version and
delete marker. Object Lock, legal holds, bucket versioning status and backup expiry remain explicit
Infra launch-drill inputs; a storage refusal leaves the durable plan retryable rather than claiming
erasure.

Prefixes are derived from both committed `data.recordings[]` media paths and the durable
`data.zaki_recording_prefixes[]` pre-upload intent list. Both sources must agree with the meeting
owner/recording/session identity; broad or mismatched entries fail before object I/O.

Public surface: `erase_meeting`, `ErasureReceipt`, `ErasureFailed`. Ports live in `ports.py`; offline
fakes in `fakes.py`; focused two-tenant proof is `tests/test_zaki_retention.py`; production-boundary
proof is `tests/test_zaki_retention_adapters.py`. No erasure HTTP route is mounted in this slice.

`ttl.py` is the scheduler-free S02a policy core. It validates explicit UTC expiry instants for audio,
transcript and summary independently, prevents an already-stored expiry from moving later, and runs
an injected-clock batch capped at 500 opaque due scopes. It returns only per-scope counts; candidate
identity and adapter errors never enter the receipt.

`ttl_adapters.py` is the S02b production composition. It selects a deterministic bounded batch from
terminal meetings whose materialized `data.zaki_retention.scope_expiries` are due, excluding erasing
meetings and scopes already recorded in `expired_scopes`. Every mutation takes the same exclusive
meeting advisory lock as full erasure, row-locks and revalidates the owner/status/unchanged deadline,
then deletes the carrier and commits its idempotency marker. Audio object prefixes are validated and
emptied before recording metadata is cleared; the marker also makes later recording writers fail
closed. Transcript rows and summary JSON are removed transactionally. A failed carrier remains due
for retry, and receipts contain counts only.

S02b still owns no scheduler, HTTP route, deployment resource, retention default, authority-layer
resolution, or restoration-horizon claim. Those product/operations choices must compose the store
explicitly and supply already-materialized per-scope deadlines.

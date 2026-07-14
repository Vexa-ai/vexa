# zaki-read.v1 — bounded Minutes read profile

The published read-only boundary consumed by the ZAKI agent. Minutes owns meeting metadata, raw
audio, transcripts and spoke summaries; consumers receive only owner-scoped, bounded responses.
This contract never grants a write path into Minutes or Brain.

> **SEALED — ROLE 8 APPROVED 2026-07-14.** The Brain / Contracts Steward approved all four WP-15
> verdicts at schema head `08cccb78`. The decision record is
> [zaki-infra #25](https://github.com/ProjectNuggets/zaki-infra/issues/25). Runtime implementations
> must preserve the three binding conditions below.

## HTTP profile

Authenticated requests use a dedicated service token and repeat the user identity in both the path
and `X-Zaki-User-Id`. Every path/header/token/item mismatch fails closed with the same `404` surface
as an absent item. Redirects are forbidden and responses are non-cacheable.

```text
GET /api/zaki/read/v1/{userId}/index?since=&limit=&cursor=
GET /api/zaki/read/v1/{userId}/item/{itemId}?variant=full|summary
GET /api/zaki/read/v1/{userId}/search?q=&limit=&cursor=
```

The index maps native records to `meeting|transcript|summary`; index and search responses contain
metadata only. Item responses contain one bounded content variant. Every success uses the common
`{items|item, truncated, next_cursor?}` envelope. Cursors are opaque, stable for the underlying
snapshot, and continue without overlap.

## Sealed WP-15 verdicts

1. `id`, `kind`, `title` and `updated_at` remain the common item metadata. `meeting_id`,
   `occurred_at`, `sensitivity`, `retention` and `capture_notice` are Minutes-profile fields; they
   remain additive v1 fields rather than widening every spoke's common envelope. The user identity
   is authoritative in the token/path/header agreement and is not repeated in response bodies.
2. Expired, erased, foreign-user and unknown items all return `404`. This avoids confirming that a
   sensitive meeting ever existed.
3. Brain distillates use `write_origin=meeting_ingest`, `source_spoke=minutes`, `source_item_id` and
   `meeting_id`. This is sufficient for scoped forget without a Brain schema change; raw transcript
   content is never a distillate.
4. Serialized `item.content` is limited to 262,144 bytes. The complete HTTP response is separately
   limited to 270,336 bytes, and the agent retains its 8-call/1-MiB per-turn budget. A full transcript
   over the content cap returns `413`; `variant=summary` remains available within the same bounds.

## Binding conditions

1. `sensitivity` and `retention` are non-nullable on every Minutes item and metadata variant. An
   unlabeled item is invalid and must never be served.
2. `/search` preserves the same non-enumeration boundary as item reads: foreign, erased and expired
   records cannot affect hit totals, result counts or cursor behavior. The expired, erased, foreign
   and unknown item paths must also avoid obviously distinguishable timing shortcuts.
3. Before activation, meeting/account erasure must purge Minutes-derived Brain rows selected by the
   sealed provenance tuple and report their content-free count in the erasure manifest. This is an
   activation gate, not permission for Minutes or the spoke to write the Brain directly.

## Privacy invariants

- `sensitivity` is `sensitive_pii` for every meeting-derived item.
- `sensitivity` and `retention` are always present; missing and `null` labels fail conformance.
- Transcript and summary reads require visible-bot evidence, capture attestation time and policy
  version, a non-expired scope-specific retention record, and an explicit tenant agent-read opt-in.
  The opt-in is evaluated before serving and is not trusted as a response-body boolean.
- A read never extends retention. Errors, logs, metrics, cursors and receipts contain no transcript
  text, service token, native Vexa identifier or storage key.
- Search authorization and retention filtering happen before pagination metadata is calculated, so
  excluded records cannot influence totals, page sizes or cursors.

Goldens under `golden/` are the executable profile. Files containing `.invalid.` are independent
negative controls and must be rejected by `validate.mjs`; transcript turn ordering and end-before-
start ranges are semantic checks layered after JSON Schema conformance.

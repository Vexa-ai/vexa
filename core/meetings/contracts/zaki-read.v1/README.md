# zaki-read.v1 — bounded Minutes read profile

The published read-only boundary consumed by the ZAKI agent. Minutes owns meeting metadata, raw
audio, transcripts and spoke summaries; consumers receive only owner-scoped, bounded responses.
This contract never grants a write path into Minutes or Brain.

> **UNSEALED — ROLE 8 REVIEW REQUIRED.** The schema records the Minutes owner's conservative
> proposal for the four WP-15 verdicts. `pnpm seal:contracts` and runtime implementation are blocked
> until the Brain / Contracts Steward approves the profile.

## HTTP profile

Authenticated requests use a dedicated service token and repeat the user identity in both the path
and `X-Zaki-User-Id`. Every path/header/token/item mismatch fails closed with the same `404` surface
as an absent item. Redirects are forbidden and responses are non-cacheable.

```text
GET /api/zaki/read/v1/{userId}/meetings?limit=&cursor=
GET /api/zaki/read/v1/{userId}/transcripts?limit=&cursor=
GET /api/zaki/read/v1/{userId}/summaries?limit=&cursor=
GET /api/zaki/read/v1/{userId}/{kind}/{itemId}?variant=full|summary
GET /api/zaki/read/v1/{userId}/search?q=&kind=&limit=&cursor=
```

Index and search responses contain metadata only. Item responses contain one bounded content
variant. Cursors are opaque, stable for the underlying snapshot, and continue without overlap.

## Proposed WP-15 verdicts

1. `id`, `kind`, `user_id`, `title`, `updated_at`, `sensitivity` and `retention` form the common
   read envelope. `meeting_id`, `occurred_at` and capture evidence are required Minutes-profile
   fields; they remain additive v1 fields rather than widening every spoke's common envelope.
2. Expired, erased, foreign-user and unknown items all return `404`. This avoids confirming that a
   sensitive meeting ever existed.
3. Brain distillates use `write_origin=meeting_ingest`, `source_spoke=minutes`, `source_item_id` and
   `meeting_id`. This is sufficient for scoped forget without a Brain schema change; raw transcript
   content is never a distillate.
4. Serialized `item.content` is limited to 262,144 bytes. The complete HTTP response is separately
   limited to 270,336 bytes, and the agent retains its 8-call/1-MiB per-turn budget. A full transcript
   over the content cap returns `413`; `variant=summary` remains available within the same bounds.

## Privacy invariants

- `sensitivity` is `sensitive_pii` for every meeting-derived item.
- Transcript and summary reads require visible-bot evidence, capture attestation time and policy
  version, a non-expired scope-specific retention record, and an explicit tenant agent-read opt-in.
- A read never extends retention. Errors, logs, metrics, cursors and receipts contain no transcript
  text, service token, native Vexa identifier or storage key.

Goldens under `golden/` are the executable profile. Files containing `.invalid.` are negative
controls and must be rejected by `validate.mjs`.

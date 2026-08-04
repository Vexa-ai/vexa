# zaki-read.v1 goldens

Synthetic, non-personal examples for the Minutes read boundary. A filename starts with the `$defs`
shape it targets. Files containing `.invalid.` are independent negative controls; all other JSON
files must conform. Privacy controls change one invariant at a time so one constraint cannot mask
another; page order, turn ordering and range are enforced by `validate.mjs` after JSON Schema
conformance. The missing-sensitivity and missing-retention controls pin Role 8's non-nullable-label
condition. `IndexResponse.transcripts.json` carries the ordered page — one occurrence whose
summary updated after its transcript — and the rising-update and tie-order controls each break one
half of the `updated_at` DESC, `id` DESC order in isolation.

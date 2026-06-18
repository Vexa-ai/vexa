# transcript.v1 — speaker-attributed segments + session envelopes

The product's core output, and the **TS↔Py seam**: the bot (TS) produces it; the collector (Py)
persists it; the gateway forwards the live bundle to the dashboard. Both languages validate against
this schema (P4).

## Shapes (`$defs`)
- **`TranscriptSegment`** — `segment_id · speaker · text · start/end (sec)` + optional `language ·
  completed · absolute_* · source` (attribution) · `confidence` · `words`.
- **Bus stream** (bot → collector): `SessionStart` → `Transcription` (confirmed batches) → `SessionEnd`.
- **`MutableBundle`** — the live `confirmed`+`pending` bundle the gateway forwards verbatim to the dashboard.

## Deliberately **not** in this contract
- **Auth is transport-layer.** The producer authenticates to the bus with its workload identity; the
  messages here carry **no token**. A contract describes *data, not credentials* (ADR-0001).
- **tenant / owner / visibility are deferred** (ADR-0003). They are added *additively* to `SessionStart`
  when sharing/multitenancy is built — optional fields, back-compatible, no refactor. The seam is the
  `canAccess` port, built then.

## Conformance
Goldens in [`golden/`](golden/) named `<Shape>.<case>.json`; `generate.mjs` (ajv) validates each against
its `$def` (the filename prefix). Run by `gate:schema`.

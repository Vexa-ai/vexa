# processed-notes.v1 — the per-meeting cleaned-transcript stream + its terminal marker

The **agent→meetings seam** (ADR 0027): the copilot worker (agent domain) XADDs cleaned notes onto
`proc:meeting:{row_id}`; meeting-api's db-writer (meetings domain) drains that stream into the
meeting row's durable `data.processed.views[]`, and agent-api's SSE relays it live. This stream
crossed a process + domain boundary unschema'd — a P4 violation — before this contract pinned it.
The agent-worker is the stream's **single writer** (P23).

## Shapes (`$defs`)
- **`Note`** — one cleaned note, `id == segment_id` (1:1 with the transcript). Re-emitting an id
  **upgrades** the note (baseline at ingest, LLM polish per beat); consumers upsert by id.
- **`Params`** — provenance (`pipeline · version · provider · model`) stamped alongside notes and
  persisted verbatim into the durable view (reproducibility).
- **`ViewEnd`** — the terminal marker: the worker emits it after its final post-`session_end` beat.
  The stream is COMPLETE at this entry — the db-writer flushes the durable view on it, the live SSE
  closes on it. A worker that dies without the marker is covered by the consumer's bounded deadline
  (P22: graceful marker + hard guarantee), never by timing luck.

## Stream-entry encoding
An XADD entry is either a note — fields `note` (JSON `Note`) + optional `params` (JSON `Params`) —
or the marker — fields `type` = `"view_end"` + optional `cursor` (the last raw `tc:meeting:{row_id}`
stream id processed, making completion auditable).

## Keying (P0 — never the native id)
`{row_id}` is the meetings-domain numeric row id, unique per (user, platform, native, run). The
sibling keys `proc:meeting:{row_id}:on` (processing desired-state flag) and `…:cursor` (the worker's
resume position in the raw feed) are agent-internal — deliberately NOT part of this contract.

## Conformance
Goldens in [`golden/`](golden/) named `<Shape>.<case>.json` (drawn from the real run-46 stream);
`validate.mjs` (ajv) validates each against its `$def` (the filename prefix). Run by `gate:schema`.

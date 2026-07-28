# alloy-stt-telemetry.v1

ALLOY: this sealed cross-runtime contract is the single owner of the ephemeral STT
queue snapshot written by the bot, the owner-scoped aggregate computed by Meeting
API, and the status response consumed through the Gateway.

The Redis carrier remains `alloy:stt:telemetry:v1:{meeting_id}`. `Snapshot` preserves
the producer fields exactly; Meeting API separately verifies that a snapshot's
`meeting_id` equals the id requested by its Redis key. `Aggregate` is server-owned.
`StatusResponse` admits only the available, disabled, and dependency-unavailable
branches represented by the goldens.

Run `node validate.mjs --check` to validate every golden. Filename prefixes select
the `$def`: `Snapshot.*`, `Aggregate.*`, and `StatusResponse.*`.

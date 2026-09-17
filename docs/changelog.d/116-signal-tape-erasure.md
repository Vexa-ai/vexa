- **Deleting a completed meeting now erases its captured-signal tapes too (#116).** `DELETE
  /meetings/{id}` already removed transcripts and recordings; the raw per-channel audio and
  diagnostics Vexa keeps internally for replay and debugging survived it, under their own storage
  prefix, until a 50 GB budget sweep happened to evict them. They now go with everything else — every
  bot session of that meeting, including a tape marked as a kept regression fixture — and the
  native-key response reports `signal_objects_deleted` beside `objects_deleted`. See
  [Meetings API](/api/meetings).
- **MCP: agents can erase a finished meeting (#116).** New `delete_meeting_artifacts` tool wrapping
  `DELETE /meetings/{platform}/{native}`, the last of the deletion tools that 0.10.6 had and the
  v0.12 MCP surface did not. It is irreversible and says so. See [Vexa MCP](/vexa-mcp).

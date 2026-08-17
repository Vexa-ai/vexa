- **Delete a completed meeting's data from the terminal UI (#1197).** Finished meetings
  (`completed`/`failed`/`stopped`) now carry a **Delete data** action that erases the transcript and
  any recordings, alongside the existing `Re-send`. It is confirm-gated and irreversible — the prompt
  names what will be destroyed and states that backups are not a recovery path. Planned meetings keep
  their existing `Delete`, which only removes a plan that has no data yet. See
  [Meetings API](/api/meetings).

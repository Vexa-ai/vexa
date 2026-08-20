- **Delete a completed meeting's transcript and recordings (#116).** `DELETE /meetings/{meeting_id}`
  and `DELETE /meetings/{platform}/{native_meeting_id}` previously answered `409` once a meeting had
  started; for a `completed`/`failed` meeting they now erase its transcript rows, transcript-derived
  notes and shares, and its recording objects in primary object storage. Deletion is owner-scoped,
  the terminal meeting row survives as lifecycle evidence, and transcript reads return `404`
  afterwards. `DELETE /recordings/{recording_id}` erases one recording's objects on its own. Erasure
  covers live storage, not backups or object-store version history, which expire under the
  operator's retention policy. See [Meetings API](/api/meetings).

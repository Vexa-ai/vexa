- **A bot writes transcript segments only for its own meeting (#1784).** Every entry a bot appends
  to the shared `transcription_segments` stream is now signed with its session's MeetingToken, and
  meeting-api's collector drops any entry that is unsigned, tampered, signed with an expired or
  foreign token, or names a meeting other than the token's. A bot from an earlier release still
  running across the upgrade writes unsigned entries, so its meeting's live transcript stops until
  the bot is sent again: **upgrade between meetings**. Tools that write to the stream directly must
  sign the same way: the entry format is transcript.v1 `StreamEntry`, with a reference signer
  (`segment_entry.py`) and a signing vector in the contract. agent-api's live-meeting list now follows only what the collector admitted. See
  [One-time steps after upgrading](/deployment#one-time-steps-after-upgrading).

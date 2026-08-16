- **Transcripts are addressable by record, empty is stated, and list filters are honest.** New
  `GET /meetings/{meeting_id}/transcript` reads a transcript by its record id, so runs the join-link
  route cannot reach — records with no `native_meeting_id`, a `native_meeting_id` that is a URL, and
  every earlier run behind a recurring link — are retrievable. Every transcript response now carries
  `empty` (and `empty_reason` when empty), so a `200` with no segments is no longer indistinguishable
  from a successful fetch. `GET /meetings` gains `updated_after` for incremental sync, documents its
  `platform` / `status` filters, and now **refuses** an unrecognized query parameter with `400`
  instead of silently ignoring it and returning the full list. See
  [Meetings API](/api/meetings#two-keys-and-when-the-native-key-is-not-enough).

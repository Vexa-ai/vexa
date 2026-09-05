- **Search your transcripts, annotate a meeting, share by id (#1456).** `GET /transcripts/search`
  runs full-text search over your own segments (the index builds itself on first boot, without
  locking the table). `POST /meetings/{id}/annotate` attaches a title and arbitrary metadata to a
  meeting during or after it, and `GET /meetings?metadata=` filters on what you wrote (16 KB and 64
  keys per meeting). Meetings can now be addressed by their row id — `POST /meetings/{id}/share`
  beside the existing platform/native pair. See [Meetings API](/api/meetings).

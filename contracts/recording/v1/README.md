# recording.v1 — chunked media upload (bot → storage)

Live today, previously unnamed. Producer: `@vexa/recording` (bot side —
RecordingService audio / VideoRecordingService x11grab). Consumer:
`meeting-api` `internal_upload_recording` (token-gated), which assembles
chunks into the final media file in S3/MinIO.

Wire shape (HTTP multipart per chunk):
- `chunk_seq` — monotonically increasing per session
- `is_final` — last chunk; triggers assembly
- `format` — `wav` | `webm` | `mp4`
- body — the chunk bytes
- auth — recording upload token (minted by meeting-api)

Oracle: replay a recorded chunk sequence against a fake receiver; the
assembled file must be byte-equal to the original media.

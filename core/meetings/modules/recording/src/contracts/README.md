# recording/src/contracts

The `recording.v1` contract this brick emits.

- [`recording-v1.md`](recording-v1.md) — the prose contract: chunk shape (`chunk_seq` + `is_final` +
  `format`), the two transports (bot HTTP multipart / desktop ingest-WS), and the master-assembly
  strategies (WebM byte-concat, WAV RIFF-merge).
- [`golden/`](golden/) — the shared golden vectors. `buildRecordingMaster` (here) and meeting-api's
  `recording_codec.py` are both pinned to these byte-for-byte, so a pass on both languages proves the
  two deliberate implementations stay in sync.

# recording/src/contracts/golden

Shared `recording.v1` master-codec golden vectors (`*.json`: `{ name, format, chunks[base64], master_len,
master_sha256 }`). The SAME vectors the Python twin (meeting-api `test_recording_golden.py`) is tested
against — a pass on both = `buildRecordingMaster` (TS) and `recording_codec.py` (Python) are provably in
sync. [`generate.mjs`](generate.mjs) regenerates them (excluded from `tsc`). Pinned by
[`../../golden.test.ts`](../../golden.test.ts).

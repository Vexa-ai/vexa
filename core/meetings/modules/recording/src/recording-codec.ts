/**
 * recording-codec — the recording.v1 MASTER codec (Node): build a final media
 * file from recording.v1 chunks. Parallels @vexa/capture-codec (the capture.v1
 * wire codec); this is the recording.v1 master codec.
 *
 * PARALLEL PATH (keep in sync). The Python twin is the pure-core module
 * `services/meeting-api/meeting_api/recording_codec.py` (NOT the finalizer — that's
 * the IO/orchestration around this). Same scope, two languages, names aligned and
 * pinned to the shared golden vectors (src/contracts/golden/):
 *      buildRecordingMaster ↔  _build_recording_master   (format dispatch)
 *      buildWebmMaster      ↔  _build_webm_master         (WebM Cluster byte-concat)
 *      buildWavMaster       ↔  _build_wav_master          (WAV RIFF header-merge)
 *      parseWavHeader       ↔  _parse_wav_header
 * Change the {webm,wav}-master logic here → mirror it there; the golden tests
 * (golden.test.ts here, test_recording_golden.py there) fail on any drift.
 * (Used by the all-Node desktop, which has no meeting-api; prod assembles via the
 * Python twin — same pattern as the desktop's node:sqlite store.)
 *
 * Two strategies, dispatched on format (the wire's recording.v1 `format`):
 *  - webm: BYTE-CONCAT in seq order. The MediaRecorder stream emits a self-
 *    describing chunk 0 (EBML + Segment + first Cluster) then Cluster-only
 *    chunks; stacking the Clusters inside the Segment yields a valid container.
 *    (ffmpeg's concat demuxer would drop the Cluster-only inputs — so NOT ffmpeg.)
 *  - wav: RIFF-aware merge — strip each chunk's 44-byte header, sum the PCM
 *    payloads, prepend one corrected master header (fmt copied from chunk 0).
 */

const WAV_HEADER_BYTES = 44;

function parseWavHeader(buf: Buffer): { fmtChunk: Buffer; declaredDataSize: number } {
  if (buf.length < WAV_HEADER_BYTES) throw new Error("WAV chunk shorter than the 44-byte header");
  if (buf.toString("ascii", 0, 4) !== "RIFF" || buf.toString("ascii", 8, 12) !== "WAVE")
    throw new Error("WAV chunk missing RIFF/WAVE magic");
  if (buf.toString("ascii", 36, 40) !== "data")
    throw new Error(`WAV chunk non-canonical: 'data' expected at offset 36, found ${JSON.stringify(buf.toString("ascii", 36, 40))}`);
  return { fmtChunk: buf.subarray(20, 36), declaredDataSize: buf.readUInt32LE(40) };
}

/** RIFF-aware merge (mirrors PulseAudioCapture._wrapWav). fmt is copied from the
 *  first chunk; all chunks must declare the same fmt (mismatch → throw). */
function buildWavMaster(chunks: Buffer[]): Buffer {
  const real = chunks.filter((c) => c.length >= WAV_HEADER_BYTES); // skip the empty final chunk
  if (real.length === 0) throw new Error("buildWavMaster requires at least one non-empty chunk");
  const { fmtChunk } = parseWavHeader(real[0]);
  const payloads: Buffer[] = [];
  real.forEach((c, i) => {
    const { fmtChunk: f } = parseWavHeader(c);
    if (!f.equals(fmtChunk)) throw new Error(`WAV fmt chunk mismatch at chunk index ${i}`);
    payloads.push(c.subarray(WAV_HEADER_BYTES));
  });
  const totalData = payloads.reduce((n, p) => n + p.length, 0);
  const header = Buffer.alloc(WAV_HEADER_BYTES);
  header.write("RIFF", 0, "ascii");
  header.writeUInt32LE(36 + totalData, 4);
  header.write("WAVE", 8, "ascii");
  header.write("fmt ", 12, "ascii");
  header.writeUInt32LE(16, 16);
  fmtChunk.copy(header, 20);
  header.write("data", 36, "ascii");
  header.writeUInt32LE(totalData, 40);
  return Buffer.concat([header, ...payloads]);
}

/** Byte-concat in seq order. The empty final chunk concatenates as a no-op. */
function buildWebmMaster(chunks: Buffer[]): Buffer {
  if (chunks.length === 0) throw new Error("buildWebmMaster requires at least one chunk");
  return Buffer.concat(chunks);
}

/**
 * Assemble `chunks` (ALREADY ordered by chunk_seq) into a single master media
 * buffer. The host writes the result to `master.<format>`.
 *
 * Note: webm output is a plain byte-concat — playable, but carries no top-level
 * duration metadata (meeting-api optionally injects it via ffmpeg; the desktop
 * keeps it dependency-free). Seeking is approximate; playback is correct.
 */
export function buildRecordingMaster(format: "webm" | "wav", chunks: Buffer[]): Buffer {
  return format === "wav" ? buildWavMaster(chunks) : buildWebmMaster(chunks);
}

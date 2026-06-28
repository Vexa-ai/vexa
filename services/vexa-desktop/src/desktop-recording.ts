/**
 * desktop-recording — the recording.v1 CONSUMER for the all-Node desktop.
 *
 * The role meeting-api plays for the bot: accept recording chunks, store them,
 * and build the final media file on stop. The extension produces chunks with
 * `@vexa/record-chunker` and sends them over the ingest WS (a recording-chunk
 * frame, `@vexa/capture-codec`); this stores each chunk to disk and, on the
 * final chunk (or session close), assembles `master.<fmt>` via the Node
 * assembler `@vexa/recording.assembleRecording`.
 *
 * Layout (mirrors meeting-api's S3 prefix, on local disk):
 *   $VEXA_RECORDINGS_DIR/<platform>/<native>/audio/<seq:06d>.<fmt>   (chunks)
 *   $VEXA_RECORDINGS_DIR/<platform>/<native>/master.<fmt>            (final file)
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { assembleRecording } from '@vexa/recording';

const REC_ROOT = process.env.VEXA_RECORDINGS_DIR || path.join(os.homedir(), '.vexa', 'recordings');
const safe = (s: string) => s.replace(/[^a-z0-9_-]/gi, '_');
const sessionDir = (platform: string, nativeId: string) => path.join(REC_ROOT, safe(platform), safe(nativeId));

export interface RecordingChunkIn { seq: number; isFinal: boolean; format: 'webm' | 'wav'; bytes: Uint8Array; }
export interface RecordingSink { write(chunk: RecordingChunkIn): void; finalize(): void; }

/** Per-session store: write chunks to disk, assemble the master on is_final (or finalize()). */
export function createRecordingSink(platform: string, nativeId: string, log: (m: string) => void): RecordingSink {
  const dir = sessionDir(platform, nativeId);
  const audioDir = path.join(dir, 'audio');
  let format: 'webm' | 'wav' = 'webm';
  let chunks = 0;
  let assembled = false;

  const finalize = () => {
    if (assembled) return;
    assembled = true;
    try {
      if (!fs.existsSync(audioDir)) return;
      const files = fs.readdirSync(audioDir).filter((f) => /^\d{6}\./.test(f)).sort();
      if (!files.length) return;
      const bufs = files.map((f) => fs.readFileSync(path.join(audioDir, f)));
      const master = assembleRecording(format, bufs);
      const out = path.join(dir, `master.${format}`);
      fs.writeFileSync(out, master);
      log(`recording: ■ master ${out} (${(master.length / 1024).toFixed(0)}KB from ${files.length} chunks)`);
    } catch (e: any) {
      log(`recording: assemble failed: ${e?.message || e}`);
    }
  };

  return {
    write({ seq, isFinal, format: fmt, bytes }) {
      format = fmt;
      if (bytes.length) {
        fs.mkdirSync(audioDir, { recursive: true });
        fs.writeFileSync(
          path.join(audioDir, `${String(seq).padStart(6, '0')}.${fmt}`),
          Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength),
        );
        chunks++;
      }
      if (isFinal) { log(`recording: final chunk seq=${seq} → assembling (${chunks} stored)`); finalize(); }
    },
    finalize,
  };
}

/** The assembled master for a meeting, if built. */
export function recordingMasterPath(platform: string, nativeId: string): { path: string; format: 'webm' | 'wav' } | null {
  const dir = sessionDir(platform, nativeId);
  for (const fmt of ['webm', 'wav'] as const) {
    const p = path.join(dir, `master.${fmt}`);
    if (fs.existsSync(p)) return { path: p, format: fmt };
  }
  return null;
}

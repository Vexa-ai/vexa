/**
 * RecordingSink adapter (2b) — the recording.v1 finalize + upload path, behind the
 * orchestrator's RecordingSink port (`close(key)`).
 *
 * recording.v1 has TWO halves (both in @vexa/recording): ACQUIRE (the browser MediaRecorder
 * tap → chunks) and DELIVER (chunk-upload over HTTP to meeting-api). The ACQUIRE side is
 * browser-resident and L4-gated (it lives in capture-bridge.ts); THIS file is the DELIVER +
 * finalize core:
 *   - chunk(seq, isFinal, format, bytes) accumulates recording.v1 chunks (createRecordingAssembler);
 *   - the empty is_final chunk OR close(key) triggers buildRecordingMaster → onMaster;
 *   - onMaster uploads the assembled master to inv.recordingUploadUrl via RecordingService.
 *
 * The orchestrator only ever calls close(key) (graceful teardown) — the robust assembly trigger,
 * since the live Stop race routinely loses the trailing MediaRecorder chunk (the WS closes before
 * it flushes). is_final is the prompt-path optimization. (See @vexa/recording's assembler doc.)
 *
 * The assembler itself is PURE (in-memory, callback-only) → L2/L3-testable without disk/socket
 * (recording.test.ts). The upload leg is L4-gated (needs a live meeting-api receiver).
 */
import {
  createRecordingAssembler,
  RecordingService,
  type RecordingMaster,
  type RecordingMasterFormat,
} from '@vexa/recording';
import type { Invocation } from './config.js';
import type { RecordingSink } from './ports.js';

/** The RecordingSink extended with the chunk ingress the capture bridge's MediaRecorder tap
 *  pumps into. The orchestrator only sees close(key); the bridge holds the BotRecordingSink to
 *  feed chunks as they arrive from the page-side recorder. */
export interface BotRecordingSink extends RecordingSink {
  /** One recording.v1 chunk for `key`: monotonic seq, the COMPLETED-signal flag, format, bytes. */
  chunk(key: string, seq: number, isFinal: boolean, format: RecordingMasterFormat, bytes: Uint8Array): void;
}

export interface RecordingSinkOptions {
  inv: Invocation;
  /** Override the master handler (tests inject this to assert the assembled master without HTTP).
   *  Default = upload to inv.recordingUploadUrl via RecordingService. */
  onMaster?: (master: RecordingMaster) => void | Promise<void>;
  log?: (msg: string) => void;
}

/** Upload an assembled master to meeting-api. The 0.11 RecordingService.upload() POSTs the
 *  finalized file to the internal upload endpoint (multipart, retry/backoff). We write the master
 *  bytes through writeBlob (it owns the temp file) then upload to inv.recordingUploadUrl. Best-
 *  effort: an upload failure is logged, never thrown (the orchestrator's teardown must not hang). */
async function uploadMaster(inv: Invocation, master: RecordingMaster, log: (m: string) => void): Promise<void> {
  const url = inv.recordingUploadUrl;
  if (!url) { log(`recording: no recordingUploadUrl — master (${master.bytes.length}B, ${master.chunks} chunks) NOT uploaded`); return; }
  try {
    const meetingId = inv.meeting_id ?? 0;
    const sessionUid = inv.connectionId ?? inv.nativeMeetingId ?? master.key;
    const svc = new RecordingService(meetingId, sessionUid);
    await svc.writeBlob(Buffer.from(master.bytes), master.format);
    await svc.upload(url, inv.internalSecret ?? '');
    await svc.cleanup().catch(() => { /* best-effort */ });
    log(`recording: uploaded master ${master.key} (${master.bytes.length}B, ${master.chunks} chunks, ${master.format})`);
  } catch (e) {
    log(`recording: upload FAILED for ${master.key}: ${String(e)}`);
  }
}

/**
 * Build the recording sink. Accumulates chunks per key; on the is_final chunk OR close(key)
 * assembles the master and hands it to onMaster (default: upload to meeting-api).
 */
export function createBotRecordingSink(opts: RecordingSinkOptions): BotRecordingSink {
  const log = opts.log ?? (() => { /* silent by default */ });
  const onMaster = opts.onMaster ?? ((master: RecordingMaster) => void uploadMaster(opts.inv, master, log));
  const assembler = createRecordingAssembler({
    onMaster: (master) => { void onMaster(master); },
    log,
  });
  return {
    chunk: (key, seq, isFinal, format, bytes) => assembler.chunk(key, seq, isFinal, format, bytes),
    close: (key) => assembler.close(key),
  };
}

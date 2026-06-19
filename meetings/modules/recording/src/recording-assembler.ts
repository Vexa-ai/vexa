/**
 * recording-assembler — the recording.v1 FINALIZE LIFECYCLE (Node).
 *
 * `recording-codec.ts` is PURE assembly: `buildRecordingMaster(format, chunks[])`
 * over chunks that are ALREADY ordered. This file owns the lifecycle AROUND that
 * call — the stateful part that turns a live stream of out-of-order recording.v1
 * chunks into one finished master:
 *
 *   accumulate chunks per session key  →  on `is_final` OR `close(key)`:
 *     order by seq → drop the empty final → buildRecordingMaster → emit via onMaster
 *
 * It lives in the module (P5 — the module owns its concern) so EVERY consumer
 * (desktop today; any future Node receiver) shares ONE lifecycle, and so the
 * golden harness can drive the lifecycle itself, not just the pure assembly. The
 * close-without-is_final path is load-bearing: the live Stop race routinely loses
 * the trailing MediaRecorder chunk (the WS closes before it flushes), so the
 * session-close is the ROBUST finalize trigger and MUST yield the same master a
 * clean is_final would — that invariant is pinned by the lifecycle goldens.
 *
 * Pure: the only effect is the injected `onMaster` callback — no WebSocket, no
 * disk. The composition root (the desktop's `desktop.ts`) supplies the disk/serve
 * adapter as `onMaster`. Because the core takes a callback and never touches a
 * socket or the filesystem, it is L2-unit-testable with an in-memory fake
 * (`recording-assembler.test.ts`) and golden-drivable (`golden-lifecycle.test.ts`).
 *
 * Parallel path (keep in sync): meeting-api's Python finalizer is the IO/
 * orchestration twin of THIS lifecycle (the cloud receiver), as recording_codec.py
 * is the twin of recording-codec.ts. The lifecycle goldens are the shared oracle.
 */
import { buildRecordingMaster } from "./recording-codec";

/** The recording.v1 master container — the format the wire's `format` carries. */
export type RecordingMasterFormat = "webm" | "wav";

/** A finished recording master, ready for the host to persist + serve. */
export interface RecordingMaster {
  /** Session key (e.g. `platform/native_meeting_id`) the chunks accumulated under. */
  key: string;
  format: RecordingMasterFormat;
  /** Assembled media file bytes (`buildRecordingMaster` output). */
  bytes: Buffer;
  /** Count of non-empty chunks that fed the master (diagnostics). */
  chunks: number;
}

export interface RecordingAssemblerOptions {
  /** Called once per session when `is_final` OR `close` assembles the master. */
  onMaster: (master: RecordingMaster) => void;
  /** Optional log sink (defaults to silent). */
  log?: (msg: string) => void;
}

/** The recording.v1 finalize lifecycle: accumulate chunks, finalize on signal. */
export interface RecordingAssembler {
  /**
   * Ingest one recording.v1 chunk for `key`. Out-of-order seqs are sorted at
   * assembly; the empty `is_final` chunk (the COMPLETED signal) triggers the
   * master build and an `onMaster` callback. After finalize the session is
   * cleared (a fresh recording reuses the key cleanly).
   */
  chunk(key: string, seq: number, isFinal: boolean, format: RecordingMasterFormat, bytes: Uint8Array): void;
  /**
   * Finalize a session on host-side close (the ingest WS dropped) even if no
   * `is_final` chunk arrived. The live Stop race routinely loses the trailing
   * MediaRecorder chunk (the WS closes before it flushes), so the session-close
   * is the ROBUST assembly trigger; `is_final` is the prompt-path optimization.
   * No-op if the session already assembled (is_final) or never had chunks.
   */
  close(key: string): void;
}

interface Session { format: RecordingMasterFormat; chunks: Map<number, Buffer>; }

/**
 * Create an in-memory recording.v1 assembler. Accumulates chunks keyed by
 * session, and on `is_final` OR `close` orders them by seq, drops the empty
 * final, builds the master (`buildRecordingMaster`), and emits it via `onMaster`.
 * Pure: the only effect is the injected callback — no disk, no socket.
 */
export function createRecordingAssembler(opts: RecordingAssemblerOptions): RecordingAssembler {
  const log = opts.log ?? (() => { /* silent */ });
  const sessions = new Map<string, Session>();

  // Assemble + emit a session's accumulated chunks, then clear it. Triggered by
  // the `is_final` chunk (prompt path) OR by close() on session end (robust path).
  function finalize(key: string, reason: string): void {
    const s = sessions.get(key);
    if (!s) return;                      // already assembled (is_final) or never started
    sessions.delete(key);                // clear before assembling so a re-record reuses the key cleanly
    const ordered = [...s.chunks.entries()].sort((a, b) => a[0] - b[0]).map(([, b]) => b);
    if (ordered.length === 0) { log(`[recording] ${key} ${reason} with no media chunks — nothing to assemble`); return; }
    try {
      const master = buildRecordingMaster(s.format, ordered);
      log(`[recording] ${key} master assembled (${reason}) — ${s.format}, ${ordered.length} chunk(s), ${master.length}B`);
      opts.onMaster({ key, format: s.format, bytes: master, chunks: ordered.length });
    } catch (e: any) {
      log(`[recording] ${key} master assembly FAILED: ${e?.message || e}`);
    }
  }

  return {
    chunk(key, seq, isFinal, format, bytes) {
      let s = sessions.get(key);
      if (!s) { s = { format, chunks: new Map() }; sessions.set(key, s); }
      // Non-empty chunks carry media; the empty is_final chunk is signal-only.
      if (bytes.length) s.chunks.set(seq, Buffer.from(bytes));
      if (isFinal) finalize(key, "is_final");
    },
    close(key) { finalize(key, "session-close"); },
  };
}

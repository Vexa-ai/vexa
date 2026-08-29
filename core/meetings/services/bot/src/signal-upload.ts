/**
 * The captured-signal tape's DELIVER half (O-TEL-1) — teardown upload to meeting-api.
 *
 * A tape that never leaves the pod is not a fixture: the container is ephemeral and its disk goes
 * with it. This is the last few seconds of a bot's life, and everything here is shaped by one rule:
 *
 *   **the upload must not be able to change how the meeting ended.**
 *
 * Concretely, and each of these is a deliberate choice against the obvious alternative:
 *
 *   • **Best-effort, never rethrows.** Every failure path — no URL, missing file, HTTP error,
 *     timeout, a broken socket — is logged and dropped. The teardown that calls this already
 *     decided the exit code; a fixture cannot revise it.
 *   • **ONE attempt.** The recording path retries with backoff because a lost chunk is a lost
 *     customer recording. A lost tape is a lost debugging aid, and retries here run inside the
 *     SIGTERM grace window — burning it on a retry risks turning a clean exit into a SIGKILL, which
 *     would cost the thing that actually matters.
 *   • **Streamed, never buffered.** Tapes are tens of MB. `readFile` then POST would double a
 *     multi-hundred-MB tape into the heap of a container that is already shutting down.
 *   • **Size-guarded.** Past the guard the tape is SKIPPED, not truncated: half a tape reads as a
 *     complete one to a replay, which is worse than no tape at all.
 */
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import http from 'node:http';
import https from 'node:https';
import type { Invocation } from './config.js';
import { DEFAULT_MAX_TAPE_BYTES, signalEvent, type CaptureSignalRecorder } from './telemetry.js';

/** The files one session leaves: the frame/hint tape, the STT round-trip sidecar, the Teams CC
 *  sidecar, the transport (CSRC) sidecar, and the observations sidecar. Mirrors meeting-api's
 *  SIGNAL_TAPE_PARTS (a closed set — the part name lands in an object key server-side). Together
 *  they are the WHOLE captured signal of a meeting, which is the point: a replay that is missing
 *  one of them cannot reproduce the decision the live bot made — nor, without the observations,
 *  what the bot noticed going wrong while it made it. */
export type TapePart = 'captured-signal' | 'stt' | 'captions' | 'csrc' | 'observations' | 'botlog' | 'transcript';

/** Deliver ONE tape file. Throws on failure; the caller logs and drops. Injected in tests. */
export type TapeUploader = (part: TapePart, filePath: string, size: number) => Promise<void>;

export interface SignalUploadOptions {
  inv: Invocation;
  /** Override the transport (tests inject a spy). Default = streamed multipart POST. */
  upload?: TapeUploader;
  /** Skip (never truncate) a tape larger than this. Defaults to the recorder's own cap. */
  maxBytes?: number;
  /** Bound on one upload. Must stay inside the container's SIGTERM→SIGKILL grace. */
  timeoutMs?: number;
}

export interface TapeUploadSummary {
  uploaded: TapePart[];
  failed: TapePart[];
  skipped: TapePart[];
}

/** 60s: long enough for tens of MB on a slow link, short enough to sit inside the stop grace. */
export const DEFAULT_UPLOAD_TIMEOUT_MS = 60_000;

/** The STT round-trip tape the recorder writes beside the frame tape (telemetry.wrapTranscribeWithTap). */
export function sttTapePath(sessionPath: string): string {
  return sessionPath.replace(/\.captured-signal\.jsonl$/, '.stt.jsonl');
}

/**
 * Upload a finished session's tape files. NEVER throws, NEVER returns a rejected promise.
 *
 * `recorder === null` (collection off) returns immediately and logs NOTHING — a deployment that
 * deliberately does not tape should not emit a line per meeting saying so.
 */
export async function uploadSignalTapes(
  recorder: CaptureSignalRecorder | null,
  opts: SignalUploadOptions,
): Promise<TapeUploadSummary> {
  const summary: TapeUploadSummary = { uploaded: [], failed: [], skipped: [] };
  if (!recorder) return summary;

  const { inv } = opts;
  const maxBytes = opts.maxBytes ?? DEFAULT_MAX_TAPE_BYTES;
  const url = inv.recordingUploadUrl;
  if (!url) {
    // The local hot-loop path (VEXA_CAPTURE_SIGNAL=1, no control plane) lands here every run, so it
    // is one quiet line naming the reason rather than a failure.
    signalEvent('tape-upload-skipped', { reason: 'no recordingUploadUrl in the invocation' });
    summary.skipped.push('captured-signal', 'stt', 'captions', 'csrc', 'observations', 'botlog', 'transcript');
    return summary;
  }
  const upload = opts.upload
    ?? streamingTapeUploader(inv, url, opts.timeoutMs ?? DEFAULT_UPLOAD_TIMEOUT_MS);

  const files: Array<[TapePart, string]> = [
    ['captured-signal', recorder.path],
    ['stt', sttTapePath(recorder.path)],
    // Teams CC. Absent on every non-Teams platform and on Teams meetings whose tenant blocks
    // captions — which is why a missing sidecar is a skip, never a failure.
    ['captions', recorder.captionsPath],
    // The transport sensor. Absent on the gmeet lane (no mix to disambiguate) and on any meeting
    // whose client never mixed server-side — a skip, for the same reason.
    ['csrc', recorder.csrcPath],
    // What the capture path noticed. Absent only where nothing was ever observed — which is
    // itself unusual enough to be worth seeing in the skip list.
    ['observations', recorder.observationsPath],
    // The bot's own running commentary. The ONE part that may arrive truncated (with the drop
    // named inside it) rather than skipped: a log is read by a human, not folded by a replay.
    ['botlog', recorder.botlogPath],
    // The transcript a viewer was left with, after every retraction. Absent when the session
    // published nothing — a silent room, or a lane that never started.
    ['transcript', recorder.transcriptPath],
  ];

  for (const [part, filePath] of files) {
    try {
      const size = await fileSize(filePath);
      if (size === null) {
        // The stt tape only exists when transcription actually ran — a normal absence, not a fault.
        summary.skipped.push(part);
        continue;
      }
      if (size === 0) {
        summary.skipped.push(part);
        continue;
      }
      if (size > maxBytes) {
        // SKIP, not truncate: a truncated tape reads as a complete one to a replay, which turns a
        // missing fixture into a lying fixture.
        signalEvent('tape-upload-skipped', { part, path: filePath, bytes: size, max_bytes: maxBytes,
                                             reason: 'larger than the upload size guard' });
        summary.skipped.push(part);
        continue;
      }
      await upload(part, filePath, size);
      signalEvent('tape-uploaded', { part, path: filePath, bytes: size,
                                     capped: recorder.isCapped() });
      summary.uploaded.push(part);
    } catch (e) {
      // Dropped, never retried into the meeting path, never rethrown.
      signalEvent('tape-upload-failed', { part, path: filePath, error: String(e) });
      summary.failed.push(part);
    }
  }
  return summary;
}

async function fileSize(path: string): Promise<number | null> {
  try {
    const s = await stat(path);
    return s.isFile() ? s.size : null;
  } catch {
    return null;
  }
}

/**
 * The default transport: ONE streamed multipart POST to meeting-api's internal upload endpoint,
 * with `media_type: "signal"` so the server takes the tape path (no JSONB fold, no master).
 *
 * The body is assembled as preamble → file stream → epilogue with an exact Content-Length, so the
 * file is never held in memory and the server sees an ordinary bounded multipart request.
 */
export function streamingTapeUploader(inv: Invocation, url: string, timeoutMs: number): TapeUploader {
  const sessionUid = inv.connectionId ?? '';
  const token = inv.internalSecret ?? '';
  const meetingId = inv.meeting_id ?? 0;

  return (part, filePath, size) => new Promise<void>((resolve, reject) => {
    const boundary = `----VexaSignalTape${Date.now()}${part}`;
    // The format is a property of the PART, and the server enforces the same table: the bot log is
    // text, everything else is JSONL. Sending 'jsonl' for a text file would land it under a key
    // whose extension lies about its contents.
    const mediaFormat = part === 'botlog' ? 'txt' : 'jsonl';
    const metadata = JSON.stringify({
      meeting_id: meetingId,
      session_uid: sessionUid,
      media_type: 'signal',
      media_format: mediaFormat,
      part,
      file_size_bytes: size,
    });
    const preamble = Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n` +
      `Content-Type: application/json\r\n\r\n${metadata}\r\n` +
      `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${part}.${mediaFormat}"\r\n` +
      `Content-Type: ${mediaFormat === 'txt' ? 'text/plain; charset=utf-8' : 'application/x-ndjson'}\r\n\r\n`,
    );
    const epilogue = Buffer.from(`\r\n--${boundary}--\r\n`);

    let target: URL;
    try {
      target = new URL(url);
    } catch (e) {
      reject(new Error(`bad recordingUploadUrl ${url}: ${String(e)}`));
      return;
    }
    const transport = target.protocol === 'https:' ? https : http;
    const req = transport.request(
      {
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port || (target.protocol === 'https:' ? 443 : 80),
        path: `${target.pathname}${target.search}`,
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': `multipart/form-data; boundary=${boundary}`,
          'Content-Length': String(preamble.length + size + epilogue.length),
        },
      },
      (res) => {
        // Drain the body either way — an undrained response keeps the socket (and the process) alive
        // past the point the teardown expects to be done.
        res.resume();
        const status = res.statusCode ?? 0;
        if (status >= 200 && status < 300) resolve();
        else reject(new Error(`upload rejected: HTTP ${status}`));
      },
    );

    let settled = false;
    const fail = (e: Error): void => {
      if (settled) return;
      settled = true;
      req.destroy();
      reject(e);
    };
    req.setTimeout(timeoutMs, () => fail(new Error(`upload timed out after ${timeoutMs}ms`)));
    req.on('error', fail);

    req.write(preamble);
    const body = createReadStream(filePath);
    body.on('error', (e) => fail(e instanceof Error ? e : new Error(String(e))));
    body.on('end', () => { if (!settled) req.end(epilogue); });
    body.pipe(req, { end: false });
  });
}

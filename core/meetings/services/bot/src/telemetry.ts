/**
 * O-TEL-1 recorder adapter — the real TelemetrySink behind the capture-bridge tap.
 *
 * Persists one captured-signal.v1 session as JSONL: a SessionHeader line, then every raw
 * frame the bridge tees in, in arrival order. The output is byte-compatible with
 * eval/replay-fixture/session.captured-signal.jsonl, so a recorded session replays through
 * the EXACT pipeline offline (replay.test.ts / eval/src/replay.mjs — O-TEL-2).
 *
 * Contract with the capture path (ports.ts TelemetrySink): captureFrame is fire-and-forget
 * and MUST NOT throw or block — frames are buffered in memory and flushed to the writer on
 * a size/time threshold, and every writer fault is swallowed + logged. A crashed bot still
 * leaves everything flushed so far on disk (crash sessions are the most valuable ones).
 *
 * The writer is a port: the local file writer here is the dev/self-host default; an S3
 * chunked writer plugs in behind the same SignalWriter shape without touching the sink.
 */
import { appendFileSync, mkdirSync } from 'node:fs';
import { appendFile } from 'node:fs/promises';
import { join } from 'node:path';
import { isMixedLanePlatform, type Invocation } from './config.js';
import { rmsOf } from './capture-bridge.js';
import type { CapturedFrame, TelemetrySink } from './ports.js';

/** Where a session's JSONL lines land. append() receives whole lines (newline-terminated). */
export interface SignalWriter {
  append(chunk: string): Promise<void>;
  end(): Promise<void>;
}

/** Local-file SignalWriter — one JSONL file per session under `dir`. */
export function fileSignalWriter(path: string): SignalWriter {
  return {
    append: (chunk) => appendFile(path, chunk, 'utf8'),
    async end() { /* nothing to finalize for a plain file */ },
  };
}

export interface CaptureSignalRecorder {
  sink: TelemetrySink;
  /** The session file path (file writer) or logical key. */
  path: string;
  /** Flush any buffered frames and stop the flush timer. Idempotent; never throws. */
  close(): Promise<void>;
}

export interface RecorderOptions {
  /** Directory for the session file (file writer). Created if absent. */
  dir?: string;
  /** Inject a writer (S3 adapter / test spy). Overrides `dir`. */
  writer?: SignalWriter;
  /** Flush cadence + buffer cap — tuned so a crash loses at most ~flushMs of signal. */
  flushMs?: number;
  maxBufferBytes?: number;
  log?: (m: string) => void;
  now?: () => number;
}

const DEFAULT_DIR = process.env.VEXA_CAPTURE_SIGNAL_DIR ?? '/tmp/captured-signal';
const DEFAULT_FLUSH_MS = 2000;
const DEFAULT_MAX_BUFFER = 1 << 20; // 1 MiB of pending JSONL

/** Build the captured-signal.v1 SessionHeader for this invocation. */
export function sessionHeader(inv: Invocation, startedAt: number): Record<string, unknown> {
  const header: Record<string, unknown> = {
    type: 'captured_signal_header',
    v: 1,
    platform: inv.platform,
    native_meeting_id: inv.nativeMeetingId ?? inv.connectionId ?? 'session',
    language: inv.language ?? null,
    lane: isMixedLanePlatform(inv.platform) ? 'mixed' : 'gmeet',
    sample_rate: 16000,
    started_at: new Date(startedAt).toISOString(),
  };
  if (inv.connectionId) header.trace_id = inv.connectionId;
  return header;
}

/** The minimal structural shape of the STT round-trip we tap (pipeline.ts Transcribe). */
type TranscribeFn = (pcm: Float32Array, prompt?: string) => Promise<{
  text: string; language: string; duration: number; segments: unknown[];
}>;

/**
 * Wrap the real STT closure so every request/response (or typed fault) lands as one JSONL
 * line next to the session file — the bisect layer between capture and assembly: a replay
 * can distinguish audio-reached-STT-and-came-back-empty against audio-never-got-there.
 * Same fire-and-forget discipline as the frame sink: a tap fault never disturbs STT.
 */
export function wrapTranscribeWithTap<T extends TranscribeFn>(
  transcribe: T,
  sessionPath: string,
  log: (m: string) => void = (m) => console.log(`[bot] stt-tap: ${m}`),
): T {
  const path = sessionPath.replace(/\.captured-signal\.jsonl$/, '.stt.jsonl');
  let faults = 0;
  const write = (rec: Record<string, unknown>): void => {
    appendFile(path, JSON.stringify(rec) + '\n', 'utf8')
      .catch((e) => { if (faults++ < 5) log(`write failed: ${String(e)}`); });
  };
  const tapped = (async (pcm: Float32Array, prompt?: string) => {
    const t0 = Date.now();
    try {
      const res = await transcribe(pcm, prompt);
      write({ at: new Date(t0).toISOString(), ms: Date.now() - t0, pcm_len: pcm.length, prompt_len: prompt?.length ?? 0, ok: true, text: res.text, language: res.language, duration: res.duration, segments: res.segments.length });
      return res;
    } catch (e) {
      const x = e as { kind?: string; status?: number; message?: string };
      write({ at: new Date(t0).toISOString(), ms: Date.now() - t0, pcm_len: pcm.length, prompt_len: prompt?.length ?? 0, ok: false, kind: x?.kind ?? 'unknown', status: x?.status, error: x?.message });
      throw e;
    }
  }) as T;
  return tapped;
}

/**
 * Create the recording TelemetrySink for one bot session. The header line is written
 * synchronously at creation (boot-time, before capture starts); frames are buffered and
 * flushed asynchronously so the capture path never waits on I/O.
 */
export function createCaptureSignalRecorder(inv: Invocation, opts: RecorderOptions = {}): CaptureSignalRecorder {
  const log = opts.log ?? ((m: string) => console.log(`[bot] capture-signal: ${m}`));
  const now = opts.now ?? Date.now;
  const startedAt = now();
  const dir = opts.dir ?? DEFAULT_DIR;
  const name = `${inv.connectionId ?? inv.nativeMeetingId ?? 'session'}.captured-signal.jsonl`;
  const path = join(dir, name);

  const headerLine = JSON.stringify(sessionHeader(inv, startedAt)) + '\n';
  let writer: SignalWriter | null = null;
  let flushing: Promise<void> = Promise.resolve();
  try {
    if (opts.writer) {
      writer = opts.writer;
      // Chain the header into the flush sequence so it always precedes frame lines.
      flushing = writer.append(headerLine).catch((e) => log(`header write failed: ${String(e)}`));
    } else {
      mkdirSync(dir, { recursive: true });
      // Header lands synchronously so even a zero-frame session leaves an attributable file.
      appendFileSync(path, headerLine, 'utf8');
      writer = fileSignalWriter(path);
    }
  } catch (e) {
    // A broken writer disables recording for the session — capture is never affected.
    log(`disabled (writer init failed): ${String(e)}`);
    writer = null;
  }

  let buf: string[] = [];
  let bufBytes = 0;
  let seq = 0;
  let faults = 0;
  const maxBuffer = opts.maxBufferBytes ?? DEFAULT_MAX_BUFFER;

  const flush = (): Promise<void> => {
    if (!writer || buf.length === 0) return flushing;
    const chunk = buf.join('');
    buf = [];
    bufBytes = 0;
    const w = writer;
    // Serialize flushes so lines never interleave out of order.
    flushing = flushing
      .then(() => w.append(chunk))
      .catch((e) => { if (faults++ < 5) log(`flush failed (frames dropped): ${String(e)}`); });
    return flushing;
  };

  const timer = writer ? setInterval(() => { void flush(); }, opts.flushMs ?? DEFAULT_FLUSH_MS) : null;
  timer?.unref?.();

  const sink: TelemetrySink = {
    captureFrame(frame: CapturedFrame): void {
      if (!writer) return;
      try {
        // The bridge tap supplies seq/rms; derive them here if a caller doesn't (ports.ts).
        const full = {
          ...frame,
          seq: frame.seq ?? seq,
          rms: frame.rms ?? rmsOf(new Float32Array(Buffer.from(frame.pcm, 'base64').buffer)),
        };
        seq = full.seq + 1;
        const line = JSON.stringify(full) + '\n';
        buf.push(line);
        bufBytes += line.length;
        if (bufBytes >= maxBuffer) void flush();
      } catch { /* must never throw into capture */ }
    },
  };

  let closed = false;
  return {
    sink,
    path,
    async close(): Promise<void> {
      if (closed) return;
      closed = true;
      if (timer) clearInterval(timer);
      try {
        await flush();
        await writer?.end();
        log(`session written: ${path} (${seq} frames)`);
      } catch (e) {
        log(`close failed: ${String(e)}`);
      }
    },
  };
}

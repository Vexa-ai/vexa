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
import { dirname, join } from 'node:path';
import { isMixedLanePlatform, type Invocation } from './config.js';
import { rmsOf, type CaptionCapableSink, type TeamsCaptionRecord } from './capture-bridge.js';
import type { CapturedFrame, HintEvent, TelemetrySink } from './ports.js';

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
  sink: CaptionCapableSink;
  /** The session file path (file writer) or logical key. */
  path: string;
  /**
   * The CC sidecar beside the tape — `<session>.captions.jsonl`.
   *
   * Captions are a SIDECAR rather than a record type inside the tape because captured-signal.v1 is
   * SEALED and defines exactly three records (SessionHeader · CapturedFrame · HintEvent). Writing a
   * fourth into the same file would make every stored session fail its own contract, and the replay
   * loader validates line by line — so the tape would stop being replayable the moment a Teams
   * meeting produced a caption. Same precedent as the STT tape (`<session>.stt.jsonl`), which is a
   * sidecar for the same reason. Adding a CaptionEvent to the contract is a v2 decision, not a
   * side effect of wiring a lane.
   */
  captionsPath: string;
  /** Bytes admitted to the tape so far (the header plus every recorded line, sidecars included). */
  bytesWritten(): number;
  /** True once the size cap stopped the writer. The MEETING is unaffected — see `admit`. */
  isCapped(): boolean;
  /** Flush any buffered frames and stop the flush timer. Idempotent; never throws. */
  close(): Promise<void>;
}

/**
 * One structured observation for the capture-signal path.
 *
 * The tape's whole lifecycle is invisible from outside: nothing in the meeting changes when it
 * starts, caps, or fails to upload — which is the design, and also why these lines are the ONLY way
 * to tell a deployment that is collecting fixtures from one that has silently stopped.
 * Events: `tape-started` · `tape-capped` · `tape-uploaded` · `tape-upload-failed`.
 */
export function signalEvent(event: string, fields: Record<string, unknown> = {}): void {
  console.log(JSON.stringify({
    level: event.endsWith('failed') || event.endsWith('invalid') ? 'warn' : 'info',
    msg: `[capture-signal] ${event}`,
    event: `capture_signal.${event.replace(/-/g, '_')}`,
    ...fields,
  }));
}

export interface RecorderOptions {
  /** Directory for the session file (file writer). Created if absent. */
  dir?: string;
  /** Inject a writer (S3 adapter / test spy). Overrides `dir`. */
  writer?: SignalWriter;
  /** Flush cadence + buffer cap — tuned so a crash loses at most ~flushMs of signal. */
  flushMs?: number;
  maxBufferBytes?: number;
  /** Hard ceiling on tape bytes; past it the writer stops and the meeting continues. */
  maxBytes?: number;
  /** Override the caption sidecar's line writer (tests assert without touching a disk). */
  captionWriter?: (line: string) => void;
  log?: (m: string) => void;
  now?: () => number;
}

const DEFAULT_DIR = process.env.VEXA_CAPTURE_SIGNAL_DIR ?? '/tmp/captured-signal';
const DEFAULT_FLUSH_MS = 2000;
const DEFAULT_MAX_BUFFER = 1 << 20; // 1 MiB of pending JSONL
/** 250 MB ≈ an hour of tape at the observed ~4 MB/min. The pod's ephemeral-storage request is the
 *  real bound; this keeps one pathological meeting from ever reaching it. */
export const DEFAULT_MAX_TAPE_BYTES = 250 * 1024 * 1024;

export function resolveMaxTapeBytes(
  raw: string | undefined = process.env.VEXA_CAPTURE_SIGNAL_MAX_BYTES,
): number {
  if (raw === undefined || raw.trim() === '') return DEFAULT_MAX_TAPE_BYTES;
  const n = Number(raw);
  // A garbled cap is NOT an instruction to record without bound — fall back loudly to the default.
  // (Same rule as meeting-api's env_flag: an unrecognized value is not an explicit opt-out, and a
  // set-but-empty env line — which .env files produce constantly — must read as unset.)
  if (!Number.isFinite(n) || n <= 0) {
    signalEvent('cap-invalid', { raw, using: DEFAULT_MAX_TAPE_BYTES });
    return DEFAULT_MAX_TAPE_BYTES;
  }
  return n;
}

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
  const captionsPath = path.replace(/\.captured-signal\.jsonl$/, '.captions.jsonl');

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

  // ── the size cap ──────────────────────────────────────────────────────────────────────────────
  // Fixture collection is default ON, so EVERY prod meeting writes into the pod's ephemeral
  // storage. Unbounded, one pathological meeting (a 6-hour room, a wedged leave) fills the disk —
  // and a bot that dies of a full disk is a lost MEETING, not just a lost fixture. So the tape has
  // a ceiling, and reaching it stops the TAPE and nothing else: no throw into capture, no leave, no
  // change to transcription or recording. A capped tape is a shorter fixture; a dead bot is an
  // incident.
  const maxBytes = opts.maxBytes ?? resolveMaxTapeBytes();
  let written = writer ? headerLine.length : 0;
  let capped = false;

  /** Gate one JSONL line against the cap. Returns whether it may be recorded. */
  const admit = (line: string): boolean => {
    if (capped) return false;
    if (written + line.length > maxBytes) {
      capped = true;
      // ONE observation, not one per dropped frame — a capped 6-hour meeting would otherwise emit
      // hundreds of thousands of identical lines and bury everything else in the pod's logs.
      signalEvent('tape-capped', {
        path, max_bytes: maxBytes, bytes: written, frames: seq, hints,
        note: 'tape stopped; the meeting continues unaffected',
      });
      return false;
    }
    written += line.length;
    return true;
  };

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

  // The caption sidecar's writer. Per-line append rather than the frame stream's buffered flush:
  // captions arrive a few per second, not one per 20 ms audio frame, so they need none of that
  // machinery — and keeping them off the frame writer means a caption can never reorder or delay an
  // audio line.
  //
  // But the appends are SERIALIZED on their own chain, and close() awaits it. Two bare
  // fire-and-forget appendFile calls can complete out of order, which would silently scramble a
  // caption stream whose whole value is its sequence — and the teardown upload reads this file
  // immediately after close(), so an unawaited tail would ship a truncated sidecar.
  let captionFaults = 0;
  let captionChain: Promise<void> = Promise.resolve();
  const writeCaption = opts.captionWriter ?? ((line: string): void => {
    captionChain = captionChain
      .then(async () => {
        // The dir already exists on the file-writer path; a session with an injected frame writer
        // may have none, so create it lazily rather than losing that session's captions.
        try { mkdirSync(dirname(captionsPath), { recursive: true }); } catch { /* best-effort */ }
        await appendFile(captionsPath, line, 'utf8');
      })
      .catch((e) => { if (captionFaults++ < 5) log(`caption write failed: ${String(e)}`); });
  });

  let hints = 0;
  let captions = 0;
  const sink: CaptionCapableSink = {
    // The mixed lane's speaker hints arrive out-of-band; without them a replay of this session
    // has audio but no way to attribute it (the gmeet lane binds its name onto the frame instead).
    captureHint(hint: HintEvent): void {
      if (!writer || capped) return;
      try {
        const line = JSON.stringify(hint) + '\n';
        if (!admit(line)) return;
        buf.push(line);
        bufBytes += line.length;
        hints++;
        if (bufBytes >= maxBuffer) void flush();
      } catch { /* must never throw into capture */ }
    },
    captureFrame(frame: CapturedFrame): void {
      if (!writer || capped) return;
      try {
        // The bridge tap supplies seq/rms; derive them here if a caller doesn't (ports.ts).
        const full = {
          ...frame,
          seq: frame.seq ?? seq,
          rms: frame.rms ?? rmsOf(new Float32Array(Buffer.from(frame.pcm, 'base64').buffer)),
        };
        const line = JSON.stringify(full) + '\n';
        // Gate BEFORE advancing seq, so the seq counter never skips numbers a replay would read as
        // dropped frames — a capped tape ends cleanly rather than looking corrupted.
        if (!admit(line)) return;
        seq = full.seq + 1;
        buf.push(line);
        bufBytes += line.length;
        if (bufBytes >= maxBuffer) void flush();
      } catch { /* must never throw into capture */ }
    },
    // Teams' own CC lane — a SECOND, independent attribution source beside the outline hints. It
    // belongs in the fixture for the same reason the hints do: a replay that has audio and outline
    // hints but not the captions cannot reproduce, offline, the tie-break a live meeting had
    // available. Written to the sidecar, counted against the SAME cap (a chatty caption stream is
    // as capable of filling the disk as a chatty audio stream).
    captureCaption(caption: TeamsCaptionRecord): void {
      if (!writer || capped) return;
      try {
        const line = JSON.stringify(caption) + '\n';
        if (!admit(line)) return;
        captions++;
        writeCaption(line);
      } catch { /* must never throw into capture */ }
    },
  };

  if (writer) signalEvent('tape-started', { path, max_bytes: maxBytes, platform: inv.platform });

  let closed = false;
  return {
    sink,
    path,
    captionsPath,
    bytesWritten: () => written,
    isCapped: () => capped,
    async close(): Promise<void> {
      if (closed) return;
      closed = true;
      if (timer) clearInterval(timer);
      try {
        await flush();
        await writer?.end();
        // Drain the caption sidecar too — the teardown upload reads this file the moment close()
        // returns, so an un-awaited tail would ship a truncated sidecar.
        await captionChain;
        log(`session written: ${path} (${seq} frames, ${hints} hints, ${captions} captions)`);
      } catch (e) {
        log(`close failed: ${String(e)}`);
      }
    },
  };
}

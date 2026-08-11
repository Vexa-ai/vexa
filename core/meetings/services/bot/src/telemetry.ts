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
import {
  rmsOf,
  type CaptionCapableSink, type CsrcCapableSink, type CsrcRecord,
  type ObservationCapableSink, type ObservationRecord, type TeamsCaptionRecord,
} from './capture-bridge.js';
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

/** The sink the bridge tees into: frames + hints (the tape) and the sidecar streams beside it. */
export type CaptureSignalSink = CaptionCapableSink & CsrcCapableSink & ObservationCapableSink;

/** The sidecar streams beside the tape. A CLOSED set, mirrored by signal-upload's TapePart and by
 *  meeting-api's SIGNAL_TAPE_PARTS — the name lands in an object key server-side. */
export type SidecarPart = 'captions' | 'csrc' | 'observations';

/** Bytes of bot log kept per session before the middle is dropped. */
export const DEFAULT_MAX_BOTLOG_BYTES = 50 * 1024 * 1024;
/** Of that budget, how much is held back for the TAIL. The head is written straight through; once
 *  it is full the tail rolls in memory, so the file that lands is head + marker + tail. */
const BOTLOG_TAIL_FRACTION = 0.2;

/**
 * Tee the bot's own log to `<session>.botlog.txt`.
 *
 * A stored fixture could always reproduce what the bot HEARD and, since the observations sidecar,
 * some of what it NOTICED — but the running commentary that a human actually debugs from (which
 * spine armed, which name resolved, which window was skipped and why) lived only in a pod that is
 * deleted minutes later. Every "why did it do that?" therefore began by asking for a log nobody
 * still had.
 *
 * THIS FILE IS THE ONE EXCEPTION to whole-or-absent. Every other artefact is skipped rather than
 * truncated, because half a tape reads to a replay exactly like a complete one — a missing fixture
 * turned into a lying fixture. A log is not replay input: nothing consumes it as a record of what
 * happened, a human reads it, and a human reading `[…] N bytes dropped […]` is not misled by it.
 * So a 6-hour meeting keeps its opening and its ending, and says so in the middle.
 */
export function startBotLogSidecar(
  path: string,
  opts: { maxBytes?: number; console?: Partial<Console>; now?: () => number } = {},
): { stop: () => void; bytes: () => number; truncated: () => boolean } {
  const maxBytes = opts.maxBytes ?? DEFAULT_MAX_BOTLOG_BYTES;
  const headBudget = Math.max(1, Math.floor(maxBytes * (1 - BOTLOG_TAIL_FRACTION)));
  const tailBudget = Math.max(1, maxBytes - headBudget);
  const target = (opts.console ?? console) as Record<string, (...a: unknown[]) => void>;
  const methods = ['log', 'info', 'warn', 'error', 'debug'] as const;
  const original: Partial<Record<string, (...a: unknown[]) => void>> = {};
  let head = 0;
  let dropped = 0;
  let tail: string[] = [];
  let tailBytes = 0;
  let stopped = false;
  let faults = 0;

  try { mkdirSync(dirname(path), { recursive: true }); } catch { /* best-effort */ }

  const write = (line: string): void => {
    if (head + line.length <= headBudget) {
      head += line.length;
      try { appendFileSync(path, line, 'utf8'); } catch (e) { if (faults++ < 3) original.error?.(`[bot] botlog write failed: ${String(e)}`); }
      return;
    }
    // Past the head budget the tail ROLLS: the newest `tailBudget` bytes survive, everything
    // between the two is counted and named rather than silently missing.
    tail.push(line);
    tailBytes += line.length;
    while (tailBytes > tailBudget && tail.length > 1) { dropped += tail[0].length; tailBytes -= tail.shift()!.length; }
  };

  const fmt = (level: string, args: unknown[]): string => {
    const body = args.map((a) => {
      if (typeof a === 'string') return a;
      try { return JSON.stringify(a); } catch { return String(a); }
    }).join(' ');
    return `${new Date(opts.now?.() ?? Date.now()).toISOString()} ${level} ${body}\n`;
  };

  for (const m of methods) {
    const prev = target[m];
    if (typeof prev !== 'function') continue;
    original[m] = prev;
    target[m] = (...args: unknown[]): void => {
      try { write(fmt(m.toUpperCase(), args)); } catch { /* a log tap must never break the bot */ }
      prev.apply(target, args);
    };
  }

  return {
    bytes: () => head + tailBytes,
    truncated: () => dropped > 0,
    stop(): void {
      if (stopped) return;
      stopped = true;
      for (const m of methods) if (original[m]) target[m] = original[m]!;
      if (tail.length === 0) return;
      const marker = `\n───── ${dropped} bytes of log dropped here (session exceeded the ${maxBytes}-byte cap; head and tail kept) ─────\n\n`;
      try { appendFileSync(path, marker + tail.join(''), 'utf8'); } catch { /* best-effort */ }
      tail = [];
    },
  };
}

/**
 * The transcript AS A VIEWER WOULD HAVE BEEN LEFT WITH IT — `<session>.transcript.jsonl`.
 *
 * The bot's egress is an append-only stream of publishes plus retractions, so the durable state is
 * a fold over that stream, not any single message in it. Every consumer performs that fold
 * (the collector upserts by segment id and deletes on retract) and until now nobody stored the
 * RESULT beside the tape — which meant answering "what did this meeting actually say?" required
 * re-running the fold from redis, in an environment that no longer exists.
 *
 * So the fold is done here, live, and written once at teardown: drafts that confirmed appear under
 * their confirmed id, drafts that were superseded do not appear at all, and a rename shows only
 * its final name. It is a SNAPSHOT, never an audit trail — the publish stream is the audit trail,
 * and `--out-writes` in the replay harness is how it is read.
 */
export function wrapTranscriptWithSnapshot<
  S extends { segment_id: string; start?: number },
  T extends { publish(segment: S): Promise<void>; retract?(ids: string[]): Promise<void> },
>(sink: T, path: string, log: (m: string) => void = (m) => console.log(`[bot] transcript-snapshot: ${m}`)): T & { writeSnapshot: () => Promise<number> } {
  const durable = new Map<string, S>();
  const wrapped = {
    ...sink,
    async publish(segment: S): Promise<void> {
      if (segment?.segment_id) durable.set(segment.segment_id, { ...segment });
      return sink.publish(segment);
    },
    ...(sink.retract
      ? {
        async retract(ids: string[]): Promise<void> {
          for (const id of ids) durable.delete(id);
          return sink.retract!(ids);
        },
      }
      : {}),
    async writeSnapshot(): Promise<number> {
      const rows = [...durable.values()].sort((a, b) => (a.start ?? 0) - (b.start ?? 0));
      try {
        mkdirSync(dirname(path), { recursive: true });
        if (rows.length > 0) await appendFile(path, rows.map((r) => JSON.stringify(r)).join('\n') + '\n', 'utf8');
        log(`${rows.length} row(s) → ${path}`);
      } catch (e) {
        log(`write failed: ${String(e)}`);
      }
      return rows.length;
    },
  };
  return wrapped as T & { writeSnapshot: () => Promise<number> };
}

/**
 * The build that produced this tape.
 *
 * A fixture whose image is unknown can prove a behaviour but never attribute it: "the bot dropped
 * the tail" is a different finding depending on whether the bot was the build with the gap-reclaim
 * fix or the one before it, and a tape collected from prod outlives the deployment that made it.
 * The env var is stamped into the image at build time (Dockerfile ARG→ENV); 'unknown' is the
 * honest answer for a locally-run bot, never a guess. An empty value reads as unset, the same rule
 * the cap uses.
 */
export function imageVersion(raw: string | undefined = process.env.VEXA_IMAGE_VERSION): string {
  const v = (raw ?? '').trim();
  return v === '' ? 'unknown' : v;
}

export interface CaptureSignalRecorder {
  sink: CaptureSignalSink;
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
  /**
   * The transport sidecar beside the tape — `<session>.csrc.jsonl`.
   *
   * A sidecar for exactly the reason the captions are one: captured-signal.v1 is SEALED at three
   * records (SessionHeader · CapturedFrame · HintEvent), the replay loader validates line by line,
   * and a fourth record type inside the tape would make every stored session fail its own contract
   * the moment a mixed-lane meeting observed a contributing source.
   *
   * One JSON object per line, in arrival order:
   *   {"type":"csrc","t":1754899200123,"csrc":3735928559,"active":true,
   *    "audioLevel":0.42,"rtpTimestamp":123456789,"lane":"mixed"}
   * `t` is epoch ms — the same clock the frames and hints carry, which is what makes an edge here
   * alignable against the audio offline. `audioLevel` / `rtpTimestamp` appear only where the UA
   * supplied them.
   */
  csrcPath: string;
  /**
   * The observations sidecar beside the tape — `<session>.observations.jsonl`.
   *
   * Every typed observation the capture path produces — a watcher reporting no signal, a caption
   * lane appearing or vanishing, the mix falling back off the server mix, the transport sensor
   * failing to read a receiver — used to exist ONLY as a log line in a pod that is deleted minutes
   * later. So a replay could reproduce what the bot heard but never what the bot NOTICED, and the
   * question a fixture is usually asked ("was the signal missing, or did we mis-read it?") had no
   * answer in the fixture. They are now data. Logging is unchanged — the sidecar is additive.
   *
   * First line is the sidecar's own mini-header, then one observation per line:
   *   {"type":"sidecar_header","v":1,"part":"observations","session_uid":"conn-1",
   *    "platform":"teams","image_version":"a1b2c3d","started_at":"2026-08-11T09:00:00.000Z"}
   *   {"type":"observation","t":1754899200123,"source":"teams-speakers","lane":"mixed",
   *    "observation":{"type":"signal-absent","platform":"teams",…}}
   * `observation` is the producer's own payload, VERBATIM — this file never re-interprets it.
   */
  observationsPath: string;
  /** The bot's own log beside the tape — `<session>.botlog.txt`. See startBotLogSidecar for why
   *  this is the ONE artefact that is truncated rather than skipped. */
  botlogPath: string;
  /** The transcript a viewer was left with — `<session>.transcript.jsonl` (retract-aware). */
  transcriptPath: string;
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
  /** Override the CSRC sidecar's line writer (tests assert without touching a disk). */
  csrcWriter?: (line: string) => void;
  /** Override the observations sidecar's line writer (tests assert without touching a disk). */
  observationWriter?: (line: string) => void;
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
    // Which build produced this tape. A prod fixture outlives the deployment that made it, and
    // "the bot dropped the tail" means something different on either side of a fix — without this
    // the tape proves a behaviour it cannot attribute. 'unknown' where the image was not stamped.
    image_version: imageVersion(),
  };
  if (inv.connectionId) header.trace_id = inv.connectionId;
  return header;
}

/** The first line of every sidecar: enough to identify the file when it is separated from its
 *  tape (an S3 listing, a curator's download, a bug report with one file attached). */
export function sidecarHeader(part: SidecarPart, inv: Invocation, startedAt: number): Record<string, unknown> {
  return {
    type: 'sidecar_header',
    v: 1,
    part,
    session_uid: inv.connectionId ?? inv.nativeMeetingId ?? 'session',
    platform: inv.platform,
    image_version: imageVersion(),
    started_at: new Date(startedAt).toISOString(),
  };
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
  const csrcPath = path.replace(/\.captured-signal\.jsonl$/, '.csrc.jsonl');
  const observationsPath = path.replace(/\.captured-signal\.jsonl$/, '.observations.jsonl');
  const botlogPath = path.replace(/\.captured-signal\.jsonl$/, '.botlog.txt');
  const transcriptPath = path.replace(/\.captured-signal\.jsonl$/, '.transcript.jsonl');

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

  /**
   * One sidecar stream beside the tape (captions · csrc · observations).
   *
   * Per-line append rather than the frame stream's buffered flush: these arrive a few per second,
   * not one per 20 ms audio frame, so they need none of that machinery — and keeping them off the
   * frame writer means a caption or a transition can never reorder or delay an audio line.
   *
   * The appends are SERIALIZED on their own chain, and close() awaits every chain. Two bare
   * fire-and-forget appendFile calls can complete out of order, which would silently scramble a
   * stream whose whole value is its sequence (an activation filed after its own deactivation
   * describes a meeting that never happened) — and the teardown upload reads these files
   * immediately after close(), so an un-awaited tail would ship a truncated sidecar.
   *
   * The mini-header is written LAZILY, on the first record: a sidecar that never gets one must not
   * exist at all, because an empty-but-present file uploads as a fixture that claims a stream we
   * never observed. It goes through `admit` like every other line, so it counts against the ONE
   * budget rather than sneaking under it.
   */
  const makeSidecar = (part: SidecarPart, filePath: string, override?: (line: string) => void) => {
    let faults = 0;
    let chain: Promise<void> = Promise.resolve();
    let headerWritten = false;
    let count = 0;
    const push = override ?? ((line: string): void => {
      chain = chain
        .then(async () => {
          // The dir already exists on the file-writer path; a session with an injected frame
          // writer may have none, so create it lazily rather than losing that session's stream.
          try { mkdirSync(dirname(filePath), { recursive: true }); } catch { /* best-effort */ }
          await appendFile(filePath, line, 'utf8');
        })
        .catch((e) => { if (faults++ < 5) log(`${part} write failed: ${String(e)}`); });
    });
    return {
      count: () => count,
      drain: (): Promise<void> => chain,
      /** Admit one record against the cap and write it (with the mini-header first). */
      write(record: unknown): void {
        if (!writer || capped) return;
        try {
          const line = JSON.stringify(record) + '\n';
          if (!headerWritten) {
            const header = JSON.stringify(sidecarHeader(part, inv, startedAt)) + '\n';
            if (!admit(header)) return;
            headerWritten = true;
            push(header);
          }
          if (!admit(line)) return;
          count++;
          push(line);
        } catch { /* must never throw into capture */ }
      },
    };
  };

  const captionSidecar = makeSidecar('captions', captionsPath, opts.captionWriter);
  const csrcSidecar = makeSidecar('csrc', csrcPath, opts.csrcWriter);
  const observationSidecar = makeSidecar('observations', observationsPath, opts.observationWriter);

  let hints = 0;
  const sink: CaptureSignalSink = {
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
    // available.
    captureCaption(caption: TeamsCaptionRecord): void { captionSidecar.write(caption); },
    // The transport's own account of who was audible when — a THIRD independent attribution
    // source, and the only one that is not an inference.
    captureCsrc(rec: CsrcRecord): void { csrcSidecar.write(rec); },
    // What the capture path NOTICED: watchers reporting no signal, a caption lane appearing or
    // vanishing, the mix falling back off the server mix, a receiver that could not be read. These
    // were log-only, in a pod deleted minutes later — so a replay could reproduce what the bot
    // heard but never what it saw go wrong, which is the question a fixture is usually asked.
    captureObservation(rec: ObservationRecord): void { observationSidecar.write(rec); },
  };

  if (writer) signalEvent('tape-started', { path, max_bytes: maxBytes, platform: inv.platform });

  let closed = false;
  return {
    sink,
    path,
    captionsPath,
    csrcPath,
    observationsPath,
    botlogPath,
    transcriptPath,
    bytesWritten: () => written,
    isCapped: () => capped,
    async close(): Promise<void> {
      if (closed) return;
      closed = true;
      if (timer) clearInterval(timer);
      try {
        await flush();
        await writer?.end();
        // Drain the sidecars too — the teardown upload reads these files the moment close()
        // returns, so an un-awaited tail would ship a truncated sidecar.
        await captionSidecar.drain();
        await csrcSidecar.drain();
        await observationSidecar.drain();
        log(`session written: ${path} (${seq} frames, ${hints} hints, ${captionSidecar.count()} captions, `
          + `${csrcSidecar.count()} csrc, ${observationSidecar.count()} observations)`);
      } catch (e) {
        log(`close failed: ${String(e)}`);
      }
    },
  };
}

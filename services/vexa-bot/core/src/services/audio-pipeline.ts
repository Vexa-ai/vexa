/**
 * services/audio-pipeline.ts — Pack U (v0.10.6) shared audio capture pipeline.
 *
 * Replaces the duplicated MediaRecorder + chunk-buffer + shutdown-flush logic
 * across googlemeet/recording.ts (1003 LOC) + msteams/recording.ts (1490 LOC)
 * + zoom/web/recording.ts (225 LOC). All three platforms now drive recording
 * through this single module.
 *
 * Architectural shape (Pack U design — see triage-log.md FOURTH-PASS):
 *
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │ LAYER 1: AudioCaptureSource (platform-specific)                  │
 *   │   - MediaRecorderCapture  ← GMeet + Teams (browser-injected)     │
 *   │   - PulseAudioCapture     ← Zoom Web (parecord subprocess)       │
 *   │   Each emits AudioChunk events: { format, data, seq, isFinal }   │
 *   └─────────────┬────────────────────────────────────────────────────┘
 *                 │
 *   ┌─────────────▼────────────────────────────────────────────────────┐
 *   │ LAYER 2: UnifiedRecordingPipeline                                │
 *   │   - Drives capture lifecycle (start/stop)                        │
 *   │   - Forwards each chunk to RecordingService.uploadChunk()        │
 *   │   - Marks final chunk with isFinal=true on graceful stop         │
 *   │   - Single error-handling path                                   │
 *   └─────────────┬────────────────────────────────────────────────────┘
 *                 │
 *   ┌─────────────▼────────────────────────────────────────────────────┐
 *   │ LAYER 3: RecordingService (existing — services/recording.ts)     │
 *   │   - Multipart POST to meeting-api /internal/recordings/upload    │
 *   │   - chunk_seq + is_final contract; meeting-api stores in MinIO   │
 *   └──────────────────────────────────────────────────────────────────┘
 *
 * Why this exists (Pack U motivation):
 *   - Pre-Pack-U, master construction lived in the bot (browser context for
 *     GMeet/Teams, Node context for Zoom). It depended on graceful-leave
 *     firing, which broke crash-mid-meeting recordings entirely.
 *   - Pack M's chunk-buffer cap (commit 43881da) shrunk the buffer that
 *     bot-side master construction relied on at graceful-leave; master
 *     uploads became ~270KB tail fragments instead of full meetings.
 *   - The fix is structural: bot uploads chunks, server-side recording_
 *     finalizer.py builds master from chunks at bot_exit_callback. This
 *     module is the bot-side half — emit chunks reliably, no master
 *     construction here.
 *
 * "No fallbacks unless explicitly decided" (Pack P / develop stage rule):
 *   - No bot-side master assembly here. None.
 *   - No "if chunk upload fails, save locally" fallback. Failed uploads
 *     surface as logJSON errors and propagate; meeting-api's reconciler
 *     handles re-fetch via chunk_seq contract.
 *   - The defensive cap on the in-flight chunk buffer (Pack M) survives
 *     here because the buffer is genuinely short-lived — chunks are
 *     uploaded immediately in ondataavailable; the cap protects against
 *     an upload backlog under network pressure.
 */

import { EventEmitter } from "events";
import { spawn, ChildProcess } from "child_process";
import { Page } from "playwright";
import { RecordingService } from "./recording";
import { logJSON } from "../utils/log";
import { log } from "../utils";
import { BotConfig } from "../types";

// ───────────────────────────────────────────────────────────────────────
// Types
// ───────────────────────────────────────────────────────────────────────

/**
 * One audio chunk emitted by an AudioCaptureSource.
 *
 * - format: 'webm' for MediaRecorder output (Opus codec); 'wav' for
 *   PulseAudio output (s16le PCM wrapped in RIFF). Server-side
 *   recording_finalizer.py dispatches on this field.
 * - data: raw bytes ready for upload. WebM chunks: chunk 0 contains the
 *   EBML init segment; chunks 1+ are Cluster-only. WAV chunks: each
 *   self-contained with a RIFF header (server strips headers from 1+
 *   when concatenating).
 * - seq: monotonically increasing from 0. Storage path becomes
 *   recordings/<user>/<storage_id>/<session>/audio/{seq:06d}.{format}.
 * - isFinal: true ONLY on the last chunk emitted via the graceful-stop
 *   path. meeting-api flips Recording.status to COMPLETED on isFinal=true.
 * - mimeType: optional, for MediaRecorder output (e.g. "audio/webm;codecs=opus").
 */
export interface AudioChunk {
  format: "webm" | "wav";
  data: Buffer;
  seq: number;
  isFinal: boolean;
  mimeType?: string;
}

/**
 * Platform-specific audio capture source.
 *
 * Implementations:
 *   - MediaRecorderCapture (GMeet + Teams) — browser-injected MediaRecorder
 *     emits a chunk per timeslice; Node side receives via page.exposeFunction
 *     callback. The browser-side helper class is BrowserMediaRecorderPipeline
 *     in utils/browser.ts (added in Pack U.2/U.3 alongside platform migration).
 *   - PulseAudioCapture (Zoom Web) — Node spawns parecord subprocess; reads
 *     stdout as raw PCM s16le; slices into 15s WAV chunks.
 *
 * Lifecycle: start() begins capture; chunks arrive as 'chunk' events; stop()
 * flushes pending chunks, emits the final chunk with isFinal=true, then
 * resolves.
 */
export interface AudioCaptureSource extends EventEmitter {
  /** Start the capture. Resolves once chunks are about to start flowing. */
  start(): Promise<void>;
  /** Stop the capture. Emits a final chunk with isFinal=true, then resolves. */
  stop(): Promise<void>;

  // EventEmitter typing for the 'chunk' event:
  on(event: "chunk", listener: (chunk: AudioChunk) => void): this;
  on(event: "error", listener: (err: Error) => void): this;
  on(event: string | symbol, listener: (...args: any[]) => void): this;
  emit(event: "chunk", chunk: AudioChunk): boolean;
  emit(event: "error", err: Error): boolean;
  emit(event: string | symbol, ...args: any[]): boolean;
}

// ───────────────────────────────────────────────────────────────────────
// UnifiedRecordingPipeline — orchestrator
// ───────────────────────────────────────────────────────────────────────

/**
 * Drives the AudioCaptureSource → RecordingService.uploadChunk pipeline.
 *
 * Single point of:
 *   - chunk-upload error handling (logJSON; never a silent fallback)
 *   - in-flight chunk count tracking (Pack M's cap=10 lives here, not
 *     per-platform; MediaRecorderCapture's browser side enforces it
 *     before emitting to keep the protective buffer in the same memory
 *     space as the producer)
 *   - shutdown-flush sequencing (await stop() resolves only after the
 *     final chunk's upload completes — no fire-and-forget)
 *
 * Construction:
 *   const pipeline = new UnifiedRecordingPipeline({
 *     source,           // AudioCaptureSource implementation
 *     recordingService, // existing RecordingService instance
 *     uploadUrl,        // meeting-api /internal/recordings/upload
 *     token,            // bot-internal auth token
 *     platform,         // 'gmeet' | 'teams' | 'zoom-web' (for log tagging)
 *   });
 *   await pipeline.start();
 *   // ... meeting runs ...
 *   await pipeline.stop();
 */
export interface UnifiedRecordingPipelineOptions {
  source: AudioCaptureSource;
  recordingService: RecordingService;
  uploadUrl: string;
  token: string;
  platform: "gmeet" | "teams" | "zoom-web";
}

export class UnifiedRecordingPipeline {
  private source: AudioCaptureSource;
  private recordingService: RecordingService;
  private uploadUrl: string;
  private token: string;
  private platform: string;
  private started = false;
  private stopping = false;
  private uploadsInFlight = 0;
  private uploadQueue: Promise<void> = Promise.resolve();

  constructor(opts: UnifiedRecordingPipelineOptions) {
    this.source = opts.source;
    this.recordingService = opts.recordingService;
    this.uploadUrl = opts.uploadUrl;
    this.token = opts.token;
    this.platform = opts.platform;
  }

  async start(): Promise<void> {
    if (this.started) {
      log(`[audio-pipeline] start() called twice for ${this.platform} — ignoring`);
      return;
    }
    this.started = true;

    // Wire up the chunk handler BEFORE starting the source so we don't
    // miss the first chunk (PulseAudio in particular can deliver bytes
    // immediately).
    this.source.on("chunk", (chunk) => {
      this._handleChunk(chunk).catch((err) => {
        logJSON({
          level: "error",
          msg: "[audio-pipeline] chunk-handler unhandled error",
          platform: this.platform,
          chunk_seq: chunk.seq,
          is_final: chunk.isFinal,
          error_message: err?.message,
          error_name: err?.name,
        });
      });
    });

    this.source.on("error", (err) => {
      logJSON({
        level: "error",
        msg: "[audio-pipeline] source error",
        platform: this.platform,
        error_message: err?.message,
        error_name: err?.name,
      });
    });

    await this.source.start();
    logJSON({
      level: "info",
      msg: "[audio-pipeline] started",
      platform: this.platform,
    });
  }

  async stop(): Promise<void> {
    if (!this.started) {
      log(`[audio-pipeline] stop() called before start() for ${this.platform} — ignoring`);
      return;
    }
    if (this.stopping) {
      log(`[audio-pipeline] stop() called twice for ${this.platform} — waiting for in-flight`);
      await this.uploadQueue;
      return;
    }
    this.stopping = true;

    // Stop the source. Implementations are responsible for emitting their
    // final chunk with isFinal=true before stop() resolves.
    await this.source.stop();

    // Wait for the upload queue to drain. The final chunk's upload must
    // complete before stop() resolves so meeting-api flips Recording.status
    // before the bot exits.
    await this.uploadQueue;

    logJSON({
      level: "info",
      msg: "[audio-pipeline] stopped",
      platform: this.platform,
      total_uploaded: this.uploadsInFlight === 0 ? "drained" : `${this.uploadsInFlight} still in-flight`,
    });
  }

  private async _handleChunk(chunk: AudioChunk): Promise<void> {
    if (!chunk.data || chunk.data.length === 0) {
      log(`[audio-pipeline] empty chunk dropped (${this.platform}, seq=${chunk.seq})`);
      return;
    }

    // Serialize uploads via the queue so chunks land in MinIO in seq order
    // even if the source emits faster than the network. Each chunk's
    // uploadChunk() call awaits its predecessor.
    this.uploadQueue = this.uploadQueue.then(async () => {
      this.uploadsInFlight += 1;
      try {
        await this.recordingService.uploadChunk(
          this.uploadUrl,
          this.token,
          chunk.data,
          chunk.seq,
          chunk.isFinal,
          chunk.format,
        );
      } catch (err: any) {
        // Surface but do NOT swallow — meeting-api's chunk_seq contract
        // means a missing chunk is detectable on the server side. The
        // recording_finalizer can build a master from the chunks that
        // DID arrive (gap = silence-padded slot in the master). No
        // bot-side fallback / local-disk save here.
        logJSON({
          level: "error",
          msg: "[audio-pipeline] chunk upload failed",
          platform: this.platform,
          chunk_seq: chunk.seq,
          is_final: chunk.isFinal,
          error_message: err?.message,
        });
      } finally {
        this.uploadsInFlight -= 1;
      }
    });

    return this.uploadQueue;
  }
}

// ───────────────────────────────────────────────────────────────────────
// PulseAudioCapture — for Zoom Web (parecord subprocess)
// ───────────────────────────────────────────────────────────────────────

/**
 * Captures audio via parecord from a PulseAudio sink monitor (typically
 * `zoom_sink.monitor`). Slices the raw s16le PCM stream into 15-second
 * WAV chunks and emits them as AudioChunk events.
 *
 * Replaces the local-disk single-WAV pattern in zoom/web/recording.ts
 * (which could lose all audio if the bot crashed mid-meeting because the
 * file never made it to the bucket — GH #296).
 *
 * WAV chunk format:
 *   - 16kHz sample rate, 1 channel, s16le PCM
 *   - 15s @ 16kHz × 1 ch × 2 bytes = 480000 bytes/chunk + 44 byte WAV header
 *   - Each chunk is a self-contained valid WAV
 *   - Server-side recording_finalizer.py concatenates by RIFF-aware merge:
 *     strips headers from chunks 1+, sums data sizes, rewrites master header.
 */
export interface PulseAudioCaptureOptions {
  /** PulseAudio source device (default: env PULSE_SINK or "zoom_sink"). */
  device?: string;
  /** Sample rate in Hz (default: 16000 — matches Whisper input). */
  sampleRate?: number;
  /** Channel count (default: 1). */
  channels?: number;
  /** Chunk duration in seconds (default: 15 — same target as MediaRecorder timeslice). */
  chunkDurationSec?: number;
}

export class PulseAudioCapture extends EventEmitter implements AudioCaptureSource {
  private process: ChildProcess | null = null;
  private device: string;
  private sampleRate: number;
  private channels: number;
  private chunkDurationSec: number;
  private bytesPerSample = 2; // s16le
  private bytesPerChunk: number;
  private buffer: Buffer = Buffer.alloc(0);
  private seq = 0;
  private stopped = false;

  constructor(opts: PulseAudioCaptureOptions = {}) {
    super();
    this.device = opts.device || process.env.PULSE_SINK || "zoom_sink";
    this.sampleRate = opts.sampleRate ?? 16000;
    this.channels = opts.channels ?? 1;
    this.chunkDurationSec = opts.chunkDurationSec ?? 15;
    this.bytesPerChunk = this.sampleRate * this.channels * this.bytesPerSample * this.chunkDurationSec;
  }

  async start(): Promise<void> {
    if (this.process) {
      log("[PulseAudioCapture] start() called twice — ignoring");
      return;
    }
    return new Promise((resolve, reject) => {
      this.process = spawn("parecord", [
        "--raw",
        "--format=s16le",
        `--rate=${this.sampleRate}`,
        `--channels=${this.channels}`,
        `--device=${this.device}.monitor`,
      ]);

      if (!this.process.stdout) {
        reject(new Error("[PulseAudioCapture] parecord has no stdout"));
        return;
      }

      let started = false;
      this.process.stdout.on("data", (buf: Buffer) => {
        if (!started) {
          log(`[PulseAudioCapture] receiving audio from ${this.device}.monitor`);
          started = true;
          resolve();
        }
        this._appendAndSlice(buf);
      });

      this.process.stderr?.on("data", (data: Buffer) => {
        const msg = data.toString().trim();
        if (msg) log(`[PulseAudioCapture] parecord stderr: ${msg}`);
      });

      this.process.on("error", (err: Error) => {
        log(`[PulseAudioCapture] parecord process error: ${err.message}`);
        if (!started) reject(err);
        this.emit("error", err);
      });

      this.process.on("exit", (code, signal) => {
        log(`[PulseAudioCapture] parecord exited: code=${code}, signal=${signal}`);
        this.process = null;
      });

      // Optimistic resolve — parecord may need a moment before audio flows
      setTimeout(() => {
        if (!started) {
          log("[PulseAudioCapture] no audio after 1s — resolving optimistically");
          resolve();
        }
      }, 1000);
    });
  }

  async stop(): Promise<void> {
    if (this.stopped) return;
    this.stopped = true;

    if (this.process) {
      try {
        this.process.kill("SIGTERM");
      } catch (err: any) {
        log(`[PulseAudioCapture] kill failed: ${err?.message || err}`);
      }
      this.process = null;
    }

    // Emit any remaining bytes as the final chunk. If buffer is empty,
    // emit an empty final chunk (server treats isFinal=true as the
    // signal to flip Recording.status, regardless of payload size).
    const tail = this.buffer;
    this.buffer = Buffer.alloc(0);
    const finalSeq = this.seq;
    this.seq += 1;
    const finalChunk: AudioChunk = {
      format: "wav",
      data: this._wrapWav(tail),
      seq: finalSeq,
      isFinal: true,
    };
    this.emit("chunk", finalChunk);
  }

  private _appendAndSlice(buf: Buffer): void {
    if (this.stopped) return;
    this.buffer = Buffer.concat([this.buffer, buf]);
    while (this.buffer.length >= this.bytesPerChunk) {
      const slice = this.buffer.subarray(0, this.bytesPerChunk);
      this.buffer = this.buffer.subarray(this.bytesPerChunk);
      const seq = this.seq;
      this.seq += 1;
      this.emit("chunk", {
        format: "wav",
        data: this._wrapWav(slice),
        seq,
        isFinal: false,
      });
    }
  }

  private _wrapWav(pcm: Buffer): Buffer {
    const header = Buffer.alloc(44);
    const dataSize = pcm.length;
    const byteRate = this.sampleRate * this.channels * this.bytesPerSample;
    const blockAlign = this.channels * this.bytesPerSample;
    header.write("RIFF", 0);
    header.writeUInt32LE(36 + dataSize, 4);
    header.write("WAVE", 8);
    header.write("fmt ", 12);
    header.writeUInt32LE(16, 16); // fmt chunk size
    header.writeUInt16LE(1, 20); // PCM format
    header.writeUInt16LE(this.channels, 22);
    header.writeUInt32LE(this.sampleRate, 24);
    header.writeUInt32LE(byteRate, 28);
    header.writeUInt16LE(blockAlign, 32);
    header.writeUInt16LE(this.bytesPerSample * 8, 34);
    header.write("data", 36);
    header.writeUInt32LE(dataSize, 40);
    return Buffer.concat([header, pcm]);
  }
}

// ───────────────────────────────────────────────────────────────────────
// MediaRecorderCapture — for Google Meet + Microsoft Teams
// ───────────────────────────────────────────────────────────────────────

/**
 * Bridges the browser-side MediaRecorder (running inside the bot's Chromium
 * tab via page.evaluate) to the Node-side AudioCaptureSource interface.
 *
 * The actual MediaRecorder boilerplate lives in
 * `utils/browser.ts:BrowserMediaRecorderPipeline` (added in Pack U.2 alongside
 * the GMeet migration) — that class runs in browser context and emits chunks
 * by calling the `__vexaSaveRecordingChunk` Node-exposed function. This Node
 * class registers that callback and re-emits chunks as `chunk` events so the
 * rest of the pipeline doesn't need to know it came from a browser.
 *
 * Why we keep MediaRecorder for these platforms (vs. switching to PulseAudio
 * for everything): GMeet/Teams audio is mixed in-browser via Web Audio API;
 * MediaRecorder taps that mix natively with native Opus encoding. PulseAudio
 * works (the bot pod has a PulseAudio sink) but loses Opus and forces an
 * ffmpeg encode hop that we don't need here.
 */
export interface MediaRecorderCaptureOptions {
  /** Playwright Page handle — the browser context where MediaRecorder lives. */
  page: Page;
  /** Bot config — passed through to browser-side init. */
  botConfig: BotConfig;
  /** sessionUid for the recording (used by browser-side for log correlation). */
  sessionUid: string;
  /** Platform tag (for log prefixes — gmeet | teams). */
  platform: "gmeet" | "teams";
  /** Timeslice in ms (default: 15000 — matches PulseAudioCapture chunk size). */
  timesliceMs?: number;
  /**
   * Browser-side initializer. Called inside page.evaluate() AFTER the
   * audio pipeline classes are exposed. The initializer is platform-specific
   * (it knows how to find media elements + create the combined audio stream
   * for its platform) but the chunk-emission side is unified via
   * BrowserMediaRecorderPipeline.
   *
   * Filled in during Pack U.2/U.3 platform migration.
   */
  startBrowserCapture: (page: Page, timesliceMs: number) => Promise<void>;
  /** Browser-side stopper. Triggers MediaRecorder.stop() + final chunk emit. */
  stopBrowserCapture: (page: Page) => Promise<void>;
}

export class MediaRecorderCapture extends EventEmitter implements AudioCaptureSource {
  private opts: MediaRecorderCaptureOptions;
  private callbacksExposed = false;
  private finalChunkPromise: Promise<void> | null = null;
  private resolveFinalChunk: (() => void) | null = null;

  constructor(opts: MediaRecorderCaptureOptions) {
    super();
    this.opts = opts;
  }

  async start(): Promise<void> {
    const { page, platform, timesliceMs = 15000 } = this.opts;

    if (!this.callbacksExposed) {
      // Expose the chunk-receiver callback. The browser side calls
      // window.__vexaSaveRecordingChunk(...) for each MediaRecorder
      // ondataavailable event; we re-emit as a 'chunk' event for the
      // pipeline.
      await page.exposeFunction(
        "__vexaSaveRecordingChunk",
        async (payload: {
          base64: string;
          chunkSeq: number;
          isFinal: boolean;
          mimeType?: string;
        }): Promise<boolean> => {
          try {
            const buf = Buffer.from(payload.base64 || "", "base64");
            // Format detection: MediaRecorder almost always emits webm/opus
            // but ogg/mp4 are theoretical fallbacks for browsers that
            // don't support webm. We only ever see "webm" in practice.
            const mt = (payload.mimeType || "").toLowerCase();
            const format: "webm" | "wav" = mt.includes("wav") ? "wav" : "webm";

            this.emit("chunk", {
              format,
              data: buf,
              seq: payload.chunkSeq,
              isFinal: !!payload.isFinal,
              mimeType: payload.mimeType,
            });

            // If this is the final chunk, resolve the stop() promise.
            if (payload.isFinal && this.resolveFinalChunk) {
              this.resolveFinalChunk();
              this.resolveFinalChunk = null;
            }

            return true;
          } catch (err: any) {
            log(`[MediaRecorderCapture:${platform}] chunk callback error: ${err?.message || err}`);
            return false;
          }
        },
      );
      this.callbacksExposed = true;
    }

    // Hand off to the platform-specific browser-side initializer.
    await this.opts.startBrowserCapture(page, timesliceMs);
  }

  async stop(): Promise<void> {
    const { page, platform } = this.opts;

    // Set up a promise that resolves when the browser side emits the final
    // chunk (via __vexaSaveRecordingChunk with isFinal=true).
    this.finalChunkPromise = new Promise<void>((resolve) => {
      this.resolveFinalChunk = resolve;
      // Safety timeout — if browser never emits the final chunk (e.g. page
      // crashed), resolve after 10s so the bot can exit.
      setTimeout(() => {
        if (this.resolveFinalChunk) {
          log(`[MediaRecorderCapture:${platform}] final chunk timeout — resolving`);
          this.resolveFinalChunk();
          this.resolveFinalChunk = null;
        }
      }, 10000);
    });

    await this.opts.stopBrowserCapture(page);
    await this.finalChunkPromise;
  }
}

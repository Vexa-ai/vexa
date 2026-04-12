import * as fs from 'fs';
import * as path from 'path';
import { log } from '../utils';
import http from 'http';
import https from 'https';

/**
 * RecordingService handles accumulating audio data and producing a WAV file.
 * Works in Node.js context — used directly for Zoom (native audio callback),
 * and receives finalized blobs from browser-based bots (Google Meet, Teams).
 *
 * Supports two modes:
 *   - Legacy (start/finalize/upload): single WAV file, uploaded at meeting end.
 *   - Incremental (startIncremental): 10-second segments uploaded to MinIO as
 *     they are produced, so at most ~10s of audio is lost if the container dies.
 */
export class RecordingService {
  private filePath: string;
  private writeStream: fs.WriteStream | null = null;
  private totalSamples: number = 0;
  private sampleRate: number;
  private channels: number;
  private isFinalized: boolean = false;
  private startTime: number = 0;

  // --- Incremental-mode state ---
  private incrementalMode: boolean = false;
  private segmentWriteStream: fs.WriteStream | null = null;
  private segmentIndex: number = 0;
  private segmentSamples: number = 0;
  private segmentPath: string = '';
  private segmentTimer: ReturnType<typeof setInterval> | null = null;
  private segmentCallbackUrl: string = '';
  private segmentToken: string = '';
  private flushIntervalMs: number = 10_000;
  private pendingUploads: Promise<void>[] = [];
  private failedSegments: Map<number, Error> = new Map();

  constructor(
    private meetingId: number,
    private sessionUid: string,
    sampleRate: number = 16000,
    channels: number = 1
  ) {
    this.sampleRate = sampleRate;
    this.channels = channels;
    this.filePath = path.join('/tmp', `recording_${meetingId}_${sessionUid}.wav`);
  }

  /**
   * Start recording — open file and write WAV header placeholder.
   */
  start(): void {
    log(`[Recording] Starting recording to ${this.filePath}`);
    this.writeStream = fs.createWriteStream(this.filePath);
    // Write a placeholder WAV header (44 bytes) — will be rewritten on finalize
    this.writeStream.write(this.createWavHeader(0));
    this.totalSamples = 0;
    this.isFinalized = false;
    this.startTime = Date.now();
  }

  /**
   * Start recording in incremental mode — segments are uploaded every
   * flushIntervalMs so only the last segment is lost on container death.
   */
  startIncremental(callbackUrl: string, token: string, flushIntervalMs: number = 10_000): void {
    log(`[Recording] Starting incremental recording (flush every ${flushIntervalMs}ms)`);
    this.incrementalMode = true;
    this.segmentCallbackUrl = callbackUrl;
    this.segmentToken = token;
    this.flushIntervalMs = flushIntervalMs;
    this.segmentIndex = 0;
    this.failedSegments = new Map();
    this.pendingUploads = [];
    this.isFinalized = false;
    this.startTime = Date.now();
    this.totalSamples = 0;

    this._openSegment();

    this.segmentTimer = setInterval(() => {
      this._rotateAndUploadSegment(false);
    }, this.flushIntervalMs);
  }

  // --- Incremental-mode internals ---

  private _segmentFilePath(index: number): string {
    const padded = String(index).padStart(4, '0');
    return path.join('/tmp', `recording_${this.meetingId}_${this.sessionUid}_seg${padded}.wav`);
  }

  private _openSegment(): void {
    this.segmentPath = this._segmentFilePath(this.segmentIndex);
    this.segmentWriteStream = fs.createWriteStream(this.segmentPath);
    // Placeholder WAV header — patched when the segment is finalized
    this.segmentWriteStream.write(this.createWavHeader(0));
    this.segmentSamples = 0;
  }

  /**
   * Rotate the current segment: close it, patch its WAV header, upload it,
   * and open a new segment so that audio capture is never blocked.
   */
  private _rotateAndUploadSegment(isFinal: boolean): void {
    if (this.segmentSamples === 0 && !isFinal) return; // nothing recorded

    // Capture current segment state
    const oldStream = this.segmentWriteStream;
    const oldPath = this.segmentPath;
    const oldSamples = this.segmentSamples;
    const oldIndex = this.segmentIndex;

    // Open next segment immediately (unless this is the final flush)
    if (!isFinal) {
      this.segmentIndex++;
      this._openSegment();
    } else {
      this.segmentWriteStream = null;
    }

    // Background: close -> patch header -> upload -> delete temp
    const uploadPromise = (async () => {
      try {
        // Close the old write stream
        await new Promise<void>((resolve, reject) => {
          if (!oldStream) { resolve(); return; }
          oldStream.end((err?: Error) => err ? reject(err) : resolve());
        });

        // Patch WAV header with correct data size
        const dataSize = oldSamples * 2;
        const headerBuffer = this.createWavHeader(dataSize);
        if (oldSamples > 0 || isFinal) {
          const fd = fs.openSync(oldPath, 'r+');
          fs.writeSync(fd, headerBuffer, 0, 44, 0);
          fs.closeSync(fd);
        }

        // Upload segment
        await this._uploadSegment(oldPath, oldIndex, oldSamples, isFinal);

        // Remove temp file
        try {
          if (fs.existsSync(oldPath)) await fs.promises.unlink(oldPath);
        } catch { /* best effort */ }
      } catch (err: any) {
        log(`[Recording] ERROR: Segment ${oldIndex} upload failed: ${err.message}`);
        this.failedSegments.set(oldIndex, err);
        // Never throw — don't interrupt audio capture
      }
    })();

    this.pendingUploads.push(uploadPromise);
  }

  /**
   * Upload a single segment file to the callback endpoint.
   */
  private async _uploadSegment(filePath: string, segmentIndex: number, samples: number, isFinal: boolean): Promise<void> {
    const maxRetries = 3;
    const baseDelayMs = 1000;
    const uploadTimeoutMs = 15_000;

    const fileData = await fs.promises.readFile(filePath);
    const fileStats = await fs.promises.stat(filePath);
    const durationSeconds = samples / this.sampleRate;

    log(`[Recording] Uploading segment ${segmentIndex} (${fileStats.size} bytes, ${durationSeconds.toFixed(1)}s, is_final=${isFinal})`);

    const boundary = `----VexaSegment${Date.now()}${segmentIndex}`;
    const metadata = JSON.stringify({
      meeting_id: this.meetingId,
      session_uid: this.sessionUid,
      format: 'wav',
      sample_rate: this.sampleRate,
      channels: this.channels,
      duration_seconds: durationSeconds,
      file_size_bytes: fileStats.size,
      segment_index: segmentIndex,
      is_final: isFinal,
    });

    const parts: Buffer[] = [];
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n`));
    parts.push(Buffer.from(metadata));
    parts.push(Buffer.from('\r\n'));
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="segment_${String(segmentIndex).padStart(4, '0')}.wav"\r\nContent-Type: audio/wav\r\n\r\n`));
    parts.push(fileData);
    parts.push(Buffer.from(`\r\n--${boundary}--\r\n`));

    const body = Buffer.concat(parts);

    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        await this._sendUpload(this.segmentCallbackUrl, this.segmentToken, boundary, body, uploadTimeoutMs);
        return;
      } catch (err: any) {
        const isLastAttempt = attempt === maxRetries;
        if (isLastAttempt) throw err;
        const delay = baseDelayMs * Math.pow(2, attempt);
        log(`[Recording] Segment ${segmentIndex} upload attempt ${attempt + 1}/${maxRetries + 1} failed: ${err.message}. Retrying in ${delay}ms...`);
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }
  }

  /**
   * Append a Float32Array audio chunk (converts to Int16 PCM and writes).
   */
  appendChunk(audioData: Float32Array): void {
    if (this.isFinalized) return;

    const pcmBuffer = this.float32ToInt16PCM(audioData);

    // Incremental mode: write to the current segment
    if (this.segmentWriteStream) {
      this.segmentWriteStream.write(pcmBuffer);
      this.segmentSamples += audioData.length;
      this.totalSamples += audioData.length;
      return;
    }

    // Legacy mode
    if (!this.writeStream) return;
    this.writeStream.write(pcmBuffer);
    this.totalSamples += audioData.length;
  }

  /**
   * Append raw PCM Int16 buffer directly (e.g., from PulseAudio capture).
   */
  appendPCMBuffer(buffer: Buffer): void {
    if (this.isFinalized) return;

    // Incremental mode: write to the current segment
    if (this.segmentWriteStream) {
      this.segmentWriteStream.write(buffer);
      const samples = buffer.length / 2; // Int16 = 2 bytes per sample
      this.segmentSamples += samples;
      this.totalSamples += samples;
      return;
    }

    // Legacy mode
    if (!this.writeStream) return;
    this.writeStream.write(buffer);
    this.totalSamples += buffer.length / 2;
  }

  /**
   * Write a blob (Buffer) directly as the recording file.
   * Used for browser-based recordings (MediaRecorder output).
   */
  async writeBlob(data: Buffer, format: string = 'wav'): Promise<string> {
    const blobPath = path.join('/tmp', `recording_${this.meetingId}_${this.sessionUid}.${format}`);
    await fs.promises.writeFile(blobPath, data);
    // Browser-based recordings may not use the default WAV path.
    // Point uploads/cleanup to the actual written blob file.
    this.filePath = blobPath;
    log(`[Recording] Wrote ${data.length} bytes blob to ${blobPath}`);
    this.isFinalized = true;
    return blobPath;
  }

  /**
   * Finalize the WAV file — close stream and rewrite header with correct size.
   * Returns the file path.
   *
   * In incremental mode: stops the timer, flushes the final segment with
   * is_final=true, and waits for all pending uploads to settle.
   */
  async finalize(): Promise<string> {
    if (this.isFinalized) return this.filePath;
    this.isFinalized = true;

    // --- Incremental mode ---
    if (this.incrementalMode) {
      // Stop the rotation timer
      if (this.segmentTimer) {
        clearInterval(this.segmentTimer);
        this.segmentTimer = null;
      }

      // Flush the final segment (is_final=true)
      this._rotateAndUploadSegment(true);

      // Wait for every pending upload (including the final one)
      await Promise.allSettled(this.pendingUploads);

      const durationSeconds = this.totalSamples / this.sampleRate;
      log(`[Recording] Incremental finalize complete: ${this.segmentIndex + 1} segments, ${durationSeconds.toFixed(1)}s total, ${this.failedSegments.size} failed`);

      return this.filePath; // not meaningful in incremental mode, but keeps the API stable
    }

    // --- Legacy single-file mode ---
    return new Promise((resolve, reject) => {
      if (!this.writeStream) {
        reject(new Error('No write stream — recording was not started'));
        return;
      }

      this.writeStream.end(() => {
        try {
          // Rewrite the WAV header with correct data size
          const dataSize = this.totalSamples * 2; // Int16 = 2 bytes per sample
          const headerBuffer = this.createWavHeader(dataSize);
          const fd = fs.openSync(this.filePath, 'r+');
          fs.writeSync(fd, headerBuffer, 0, 44, 0);
          fs.closeSync(fd);

          const stats = fs.statSync(this.filePath);
          const durationSeconds = this.totalSamples / this.sampleRate;
          log(`[Recording] Finalized: ${this.filePath} (${stats.size} bytes, ${durationSeconds.toFixed(1)}s, ${this.totalSamples} samples)`);
          resolve(this.filePath);
        } catch (err) {
          reject(err);
        }
      });
    });
  }

  /**
   * Upload the finalized recording to the meeting-api internal upload endpoint.
   * Retries up to 3 times with exponential backoff on transient failures.
   *
   * No-op in incremental mode — segments are already uploaded during recording.
   */
  async upload(callbackUrl: string, token: string): Promise<void> {
    if (this.incrementalMode) {
      log('[Recording] Incremental mode — upload() is a no-op (segments already uploaded)');
      return;
    }

    const maxRetries = 3;
    const baseDelayMs = 1000;
    const uploadTimeoutMs = 30_000;

    const filePath = this.isFinalized ? this.filePath : await this.finalize();
    const fileData = await fs.promises.readFile(filePath);
    const fileStats = await fs.promises.stat(filePath);
    const format = path.extname(filePath).slice(1) || 'wav';
    const durationSeconds = format === 'wav'
      ? this.totalSamples / this.sampleRate
      : (this.startTime > 0 ? (Date.now() - this.startTime) / 1000 : undefined);

    log(`[Recording] Uploading ${fileStats.size} bytes to ${callbackUrl}`);

    const boundary = `----VexaRecording${Date.now()}`;
    const metadata = JSON.stringify({
      meeting_id: this.meetingId,
      session_uid: this.sessionUid,
      format: format,
      sample_rate: this.sampleRate,
      channels: this.channels,
      duration_seconds: durationSeconds,
      file_size_bytes: fileStats.size,
    });

    // Build multipart body
    const parts: Buffer[] = [];
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n`));
    parts.push(Buffer.from(metadata));
    parts.push(Buffer.from('\r\n'));
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="recording.${format}"\r\nContent-Type: audio/${format}\r\n\r\n`));
    parts.push(fileData);
    parts.push(Buffer.from(`\r\n--${boundary}--\r\n`));

    const body = Buffer.concat(parts);

    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        await this._sendUpload(callbackUrl, token, boundary, body, uploadTimeoutMs);
        return; // Success
      } catch (err: any) {
        const isLastAttempt = attempt === maxRetries;
        if (isLastAttempt) {
          log(`[Recording] ERROR: Upload failed after ${maxRetries + 1} attempts: ${err.message}`);
          throw err;
        }
        const delay = baseDelayMs * Math.pow(2, attempt);
        log(`[Recording] Upload attempt ${attempt + 1}/${maxRetries + 1} failed: ${err.message}. Retrying in ${delay}ms...`);
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }
  }

  private _sendUpload(callbackUrl: string, token: string, boundary: string, body: Buffer, timeoutMs: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const url = new URL(callbackUrl);
      const transport = url.protocol === 'https:' ? https : http;
      const req = transport.request(
        {
          hostname: url.hostname,
          port: url.port,
          path: url.pathname,
          method: 'POST',
          timeout: timeoutMs,
          headers: {
            'Content-Type': `multipart/form-data; boundary=${boundary}`,
            'Content-Length': body.length,
            'Authorization': `Bearer ${token}`,
          },
        },
        (res) => {
          let responseData = '';
          res.on('data', (chunk) => { responseData += chunk; });
          res.on('end', () => {
            if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
              log(`[Recording] Upload successful: ${res.statusCode}`);
              resolve();
            } else {
              log(`[Recording] Upload failed: ${res.statusCode} - ${responseData}`);
              reject(new Error(`Upload failed with status ${res.statusCode}: ${responseData}`));
            }
          });
        }
      );
      req.on('timeout', () => {
        req.destroy();
        reject(new Error(`Upload timed out after ${timeoutMs}ms`));
      });
      req.on('error', (err) => {
        log(`[Recording] Upload error: ${err.message}`);
        reject(err);
      });
      req.write(body);
      req.end();
    });
  }

  /**
   * Clean up temporary files, including any lingering segment files.
   */
  async cleanup(): Promise<void> {
    // Clean up main legacy file
    try {
      if (fs.existsSync(this.filePath)) {
        await fs.promises.unlink(this.filePath);
        log(`[Recording] Cleaned up ${this.filePath}`);
      }
    } catch (err: any) {
      log(`[Recording] Cleanup error: ${err.message}`);
    }

    // Clean up any lingering segment files (incremental mode)
    if (this.incrementalMode) {
      for (let i = 0; i <= this.segmentIndex; i++) {
        const segPath = this._segmentFilePath(i);
        try {
          if (fs.existsSync(segPath)) {
            await fs.promises.unlink(segPath);
            log(`[Recording] Cleaned up segment ${segPath}`);
          }
        } catch (err: any) {
          log(`[Recording] Segment cleanup error (${segPath}): ${err.message}`);
        }
      }
    }
  }

  getFilePath(): string {
    return this.filePath;
  }

  getStartTime(): number {
    return this.startTime;
  }

  getDurationSeconds(): number {
    return this.totalSamples / this.sampleRate;
  }

  getFileSizeBytes(): number {
    try {
      return fs.statSync(this.filePath).size;
    } catch {
      return 0;
    }
  }

  // --- WAV helpers ---

  private createWavHeader(dataSize: number): Buffer {
    const header = Buffer.alloc(44);
    const byteRate = this.sampleRate * this.channels * 2; // 16-bit = 2 bytes
    const blockAlign = this.channels * 2;

    header.write('RIFF', 0);
    header.writeUInt32LE(36 + dataSize, 4);
    header.write('WAVE', 8);
    header.write('fmt ', 12);
    header.writeUInt32LE(16, 16);       // Subchunk1Size (PCM)
    header.writeUInt16LE(1, 20);        // AudioFormat (PCM)
    header.writeUInt16LE(this.channels, 22);
    header.writeUInt32LE(this.sampleRate, 24);
    header.writeUInt32LE(byteRate, 28);
    header.writeUInt16LE(blockAlign, 32);
    header.writeUInt16LE(16, 34);       // BitsPerSample
    header.write('data', 36);
    header.writeUInt32LE(dataSize, 40);

    return header;
  }

  private float32ToInt16PCM(float32Data: Float32Array): Buffer {
    const buffer = Buffer.alloc(float32Data.length * 2);
    for (let i = 0; i < float32Data.length; i++) {
      const s = Math.max(-1, Math.min(1, float32Data[i]));
      const val = s < 0 ? s * 0x8000 : s * 0x7FFF;
      buffer.writeInt16LE(Math.round(val), i * 2);
    }
    return buffer;
  }
}

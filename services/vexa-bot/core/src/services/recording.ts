import * as fs from 'fs';
import * as path from 'path';
import { log } from '../utils';
import { logJSON } from '../utils/log';
import http from 'http';
import https from 'https';

/**
 * RecordingService handles accumulating audio data and producing a WAV file.
 * Works in Node.js context — used directly for Zoom (native audio callback),
 * and receives finalized blobs from browser-based bots (Google Meet, Teams).
 */
export class RecordingService {
  private filePath: string;
  private writeStream: fs.WriteStream | null = null;
  private totalSamples: number = 0;
  private sampleRate: number;
  private channels: number;
  private isFinalized: boolean = false;
  private startTime: number = 0;

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
   * Append a Float32Array audio chunk (converts to Int16 PCM and writes).
   */
  appendChunk(audioData: Float32Array): void {
    if (!this.writeStream || this.isFinalized) return;

    const pcmBuffer = this.float32ToInt16PCM(audioData);
    this.writeStream.write(pcmBuffer);
    this.totalSamples += audioData.length;
  }

  /**
   * Append raw PCM Int16 buffer directly (e.g., from PulseAudio capture).
   */
  appendPCMBuffer(buffer: Buffer): void {
    if (!this.writeStream || this.isFinalized) return;

    this.writeStream.write(buffer);
    this.totalSamples += buffer.length / 2; // Int16 = 2 bytes per sample
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
   */
  async finalize(): Promise<string> {
    if (this.isFinalized) return this.filePath;
    this.isFinalized = true;

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
   */
  async upload(callbackUrl: string, token: string): Promise<void> {
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
          // v0.10.5 Pack G.1 — recording-loss diagnostic. The structured
          // record carries enough fields for the operator to recover from
          // S3 (meeting_id + session_uid + format) without parsing the
          // free-text message.
          logJSON({
            level: "error",
            msg: "[Recording] Upload failed permanently",
            attempts: maxRetries + 1,
            error_message: err?.message,
            error_name: err?.name,
            file_size_bytes: fileStats.size,
            duration_seconds: durationSeconds,
            recording_meeting_id: this.meetingId,
            recording_session_uid: this.sessionUid,
          });
          throw err;
        }
        const delay = baseDelayMs * Math.pow(2, attempt);
        logJSON({
          level: "warn",
          msg: "[Recording] Upload attempt failed; will retry",
          attempt: attempt + 1,
          attempts_max: maxRetries + 1,
          retry_delay_ms: delay,
          error_message: err?.message,
          recording_meeting_id: this.meetingId,
          recording_session_uid: this.sessionUid,
        });
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }
  }

  /**
   * Upload a single recording chunk to the meeting-api internal endpoint.
   *
   * Pack B (issue #218): the bot's MediaRecorder emits a chunk every N ms;
   * each chunk lands in MinIO immediately so an ungraceful exit (SIGKILL)
   * leaves the already-uploaded chunks durable. Set `isFinal=true` on the
   * last chunk (the one sent from the graceful-shutdown path) so meeting-api
   * flips Recording.status from IN_PROGRESS to COMPLETED and fires the
   * recording.completed webhook.
   *
   * chunk_seq is monotonically increasing from 0. Storage path ends up at
   * `recordings/<user>/<storage_id>/<session>/<chunk_seq:06d>.<format>`.
   *
   * Uses the same multipart body shape as upload() — meeting-api receives
   * identical field names, just with chunk_seq + is_final=false for
   * intermediate chunks.
   */
  async uploadChunk(
    callbackUrl: string,
    token: string,
    chunkData: Buffer,
    chunkSeq: number,
    isFinal: boolean,
    format: string = 'webm',
  ): Promise<void> {
    const uploadTimeoutMs = 30_000;
    const durationSeconds = this.startTime > 0 ? (Date.now() - this.startTime) / 1000 : undefined;

    const boundary = `----VexaRecordingChunk${Date.now()}${chunkSeq}`;
    const metadata = JSON.stringify({
      meeting_id: this.meetingId,
      session_uid: this.sessionUid,
      format: format,
      sample_rate: this.sampleRate,
      channels: this.channels,
      duration_seconds: durationSeconds,
      file_size_bytes: chunkData.length,
      chunk_seq: chunkSeq,
      is_final: isFinal,
    });

    const parts: Buffer[] = [];
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n`));
    parts.push(Buffer.from(metadata));
    parts.push(Buffer.from('\r\n'));
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="chunk_seq"\r\n\r\n${chunkSeq}\r\n`));
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="is_final"\r\n\r\n${isFinal ? 'true' : 'false'}\r\n`));
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="recording.${chunkSeq}.${format}"\r\nContent-Type: audio/${format}\r\n\r\n`));
    parts.push(chunkData);
    parts.push(Buffer.from(`\r\n--${boundary}--\r\n`));

    const body = Buffer.concat(parts);

    const maxRetries = 2;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        await this._sendUpload(callbackUrl, token, boundary, body, uploadTimeoutMs);
        logJSON({
          level: "info",
          msg: "[Recording] Chunk uploaded",
          chunk_seq: chunkSeq,
          is_final: isFinal,
          chunk_size_bytes: chunkData.length,
          recording_meeting_id: this.meetingId,
          recording_session_uid: this.sessionUid,
        });
        return;
      } catch (err: any) {
        if (attempt === maxRetries) {
          // v0.10.5 Pack G.1 — chunk-loss diagnostic. Whether is_final
          // or not is load-bearing here: a lost final chunk leaves the
          // meeting Recording row stuck IN_PROGRESS forever (Pack E.1's
          // outbox is the durable fix on the meeting-api side; this log
          // is the bot-side audit record).
          logJSON({
            level: "error",
            msg: "[Recording] Chunk upload failed permanently",
            chunk_seq: chunkSeq,
            is_final: isFinal,
            chunk_size_bytes: chunkData.length,
            attempts: maxRetries + 1,
            error_message: err?.message,
            error_name: err?.name,
            recording_meeting_id: this.meetingId,
            recording_session_uid: this.sessionUid,
          });
          throw err;
        }
        const delay = 500 * Math.pow(2, attempt);
        logJSON({
          level: "warn",
          msg: "[Recording] Chunk upload attempt failed; will retry",
          chunk_seq: chunkSeq,
          is_final: isFinal,
          attempt: attempt + 1,
          attempts_max: maxRetries + 1,
          retry_delay_ms: delay,
          error_message: err?.message,
          recording_meeting_id: this.meetingId,
          recording_session_uid: this.sessionUid,
        });
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
              logJSON({
                level: "info",
                msg: "[Recording] Upload successful",
                http_status: res.statusCode,
                recording_meeting_id: this.meetingId,
                recording_session_uid: this.sessionUid,
              });
              resolve();
            } else {
              // v0.10.5 Pack G.1 — capture status code distinct from
              // message body so operators can route 4xx (caller bug)
              // vs 5xx (transient platform) automatically.
              logJSON({
                level: "warn",
                msg: "[Recording] Upload returned non-2xx",
                http_status: res.statusCode,
                response_body_preview: typeof responseData === "string"
                  ? responseData.slice(0, 500)
                  : "",
                recording_meeting_id: this.meetingId,
                recording_session_uid: this.sessionUid,
              });
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
   * Clean up temporary files.
   */
  async cleanup(): Promise<void> {
    try {
      if (fs.existsSync(this.filePath)) {
        await fs.promises.unlink(this.filePath);
        log(`[Recording] Cleaned up ${this.filePath}`);
      }
    } catch (err: any) {
      log(`[Recording] Cleanup error: ${err.message}`);
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

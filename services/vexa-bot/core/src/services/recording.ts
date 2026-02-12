import * as fs from 'fs';
import * as path from 'path';
import { log } from '../utils';
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
   * Upload the finalized recording to the bot-manager's internal upload endpoint.
   */
  async upload(callbackUrl: string, token: string): Promise<void> {
    const filePath = this.isFinalized ? this.filePath : await this.finalize();
    const fileData = await fs.promises.readFile(filePath);
    const fileStats = await fs.promises.stat(filePath);
    const format = path.extname(filePath).slice(1) || 'wav';
    const durationSeconds = format === 'wav' ? this.totalSamples / this.sampleRate : undefined;

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
    // Metadata part
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n`));
    parts.push(Buffer.from(metadata));
    parts.push(Buffer.from('\r\n'));
    // File part
    parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="recording.${format}"\r\nContent-Type: audio/${format}\r\n\r\n`));
    parts.push(fileData);
    parts.push(Buffer.from(`\r\n--${boundary}--\r\n`));

    const body = Buffer.concat(parts);

    return new Promise((resolve, reject) => {
      const url = new URL(callbackUrl);
      const transport = url.protocol === 'https:' ? https : http;
      const req = transport.request(
        {
          hostname: url.hostname,
          port: url.port,
          path: url.pathname,
          method: 'POST',
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

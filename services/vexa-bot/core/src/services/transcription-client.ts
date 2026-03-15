import { log } from '../utils';

export interface TranscriptionSegment {
  start: number;
  end: number;
  text: string;
}

export interface TranscriptionResult {
  text: string;
  language: string;
  language_probability?: number;
  duration: number;
  segments: TranscriptionSegment[];
}

export interface TranscriptionClientConfig {
  /** Base URL of transcription-service, e.g. "http://localhost:8083" */
  serviceUrl: string;
  /** Optional bearer token for authentication */
  apiToken?: string;
  /** Max retry attempts for transient failures. Default: 3 */
  maxRetries?: number;
  /** Base delay between retries in ms. Default: 1000 */
  retryDelayMs?: number;
  /** Sample rate of input audio. Default: 16000 */
  sampleRate?: number;
}

/**
 * HTTP client for the transcription-service.
 * Converts Float32Array audio to WAV, sends as multipart form,
 * and returns transcription results.
 */
export class TranscriptionClient {
  private serviceUrl: string;
  private apiToken: string | undefined;
  private maxRetries: number;
  private retryDelayMs: number;
  private sampleRate: number;

  constructor(config: TranscriptionClientConfig) {
    // Ensure serviceUrl ends with the transcriptions endpoint
    this.serviceUrl = config.serviceUrl.replace(/\/+$/, '');
    if (!this.serviceUrl.endsWith('/v1/audio/transcriptions')) {
      this.serviceUrl += '/v1/audio/transcriptions';
    }
    this.apiToken = config.apiToken;
    this.maxRetries = config.maxRetries ?? 3;
    this.retryDelayMs = config.retryDelayMs ?? 1000;
    this.sampleRate = config.sampleRate ?? 16000;
  }

  /**
   * Transcribe a Float32Array audio buffer.
   * Converts to WAV, POSTs to transcription-service, returns parsed result.
   * Retries on transient failures (503, network errors).
   */
  async transcribe(audioData: Float32Array, language?: string): Promise<TranscriptionResult> {
    const wavBuffer = this.float32ToWav(audioData);

    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        const result = await this.sendRequest(wavBuffer, language);
        return result;
      } catch (err: any) {
        const isTransient = err.statusCode === 503 || err.statusCode === 429 || !err.statusCode;
        const isLastAttempt = attempt === this.maxRetries;

        if (isTransient && !isLastAttempt) {
          const delay = this.retryDelayMs * Math.pow(2, attempt);
          log(`[TranscriptionClient] Transient error (attempt ${attempt + 1}/${this.maxRetries + 1}): ${err.message}. Retrying in ${delay}ms...`);
          await new Promise(resolve => setTimeout(resolve, delay));
          continue;
        }

        // Non-transient error or exhausted retries
        log(`[TranscriptionClient] Transcription failed after ${attempt + 1} attempts: ${err.message}`);
        throw err;
      }
    }

    // Should never reach here, but TypeScript needs it
    throw new Error('Transcription failed: exhausted retries');
  }

  /**
   * Send the WAV buffer to the transcription-service as multipart form data.
   */
  private async sendRequest(wavBuffer: Buffer, language?: string): Promise<TranscriptionResult> {
    // Build multipart form data manually (no external dependency needed)
    const boundary = `----FormBoundary${Date.now().toString(36)}`;

    const parts: Buffer[] = [];

    // File part
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n` +
      `Content-Type: audio/wav\r\n\r\n`
    ));
    parts.push(wavBuffer);
    parts.push(Buffer.from('\r\n'));

    // Model part (required by OpenAI-compatible API)
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="model"\r\n\r\n` +
      `whisper-1\r\n`
    ));

    // Response format part
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="response_format"\r\n\r\n` +
      `verbose_json\r\n`
    ));

    // Language part (if specified)
    if (language) {
      parts.push(Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="language"\r\n\r\n` +
        `${language}\r\n`
      ));
    }

    // End boundary
    parts.push(Buffer.from(`--${boundary}--\r\n`));

    const body = Buffer.concat(parts);

    const headers: Record<string, string> = {
      'Content-Type': `multipart/form-data; boundary=${boundary}`,
    };
    if (this.apiToken) {
      headers['Authorization'] = `Bearer ${this.apiToken}`;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    try {
      const response = await fetch(this.serviceUrl, {
        method: 'POST',
        headers,
        body,
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => 'Unable to read error response');
        const err: any = new Error(`Transcription service returned HTTP ${response.status}: ${errorText}`);
        err.statusCode = response.status;
        throw err;
      }

      const data = await response.json() as any;

      return {
        text: data.text || '',
        language: data.language || language || 'unknown',
        language_probability: data.language_probability ?? 0,
        duration: data.duration || 0,
        segments: (data.segments || []).map((s: any) => ({
          start: s.start || 0,
          end: s.end || 0,
          text: s.text || '',
        })),
      };
    } finally {
      clearTimeout(timeoutId);
    }
  }

  /**
   * Convert Float32Array audio samples to a WAV file buffer.
   * Output: 16-bit PCM, mono, at this.sampleRate (default 16kHz).
   */
  private float32ToWav(samples: Float32Array): Buffer {
    const numChannels = 1;
    const bitsPerSample = 16;
    const bytesPerSample = bitsPerSample / 8;
    const dataSize = samples.length * bytesPerSample;
    const headerSize = 44;
    const buffer = Buffer.alloc(headerSize + dataSize);

    // RIFF header
    buffer.write('RIFF', 0);
    buffer.writeUInt32LE(36 + dataSize, 4);
    buffer.write('WAVE', 8);

    // fmt sub-chunk
    buffer.write('fmt ', 12);
    buffer.writeUInt32LE(16, 16);              // Sub-chunk size
    buffer.writeUInt16LE(1, 20);               // PCM format
    buffer.writeUInt16LE(numChannels, 22);     // Mono
    buffer.writeUInt32LE(this.sampleRate, 24);  // Sample rate
    buffer.writeUInt32LE(this.sampleRate * numChannels * bytesPerSample, 28); // Byte rate
    buffer.writeUInt16LE(numChannels * bytesPerSample, 32); // Block align
    buffer.writeUInt16LE(bitsPerSample, 34);   // Bits per sample

    // data sub-chunk
    buffer.write('data', 36);
    buffer.writeUInt32LE(dataSize, 40);

    // Convert Float32 [-1, 1] to Int16
    let offset = headerSize;
    for (let i = 0; i < samples.length; i++) {
      let sample = samples[i];
      // Clamp to [-1, 1]
      sample = Math.max(-1, Math.min(1, sample));
      // Convert to 16-bit integer
      const int16 = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
      buffer.writeInt16LE(Math.round(int16), offset);
      offset += 2;
    }

    return buffer;
  }
}

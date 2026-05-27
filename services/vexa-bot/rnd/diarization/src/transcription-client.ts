/**
 * Minimal transcription client — POST WAV to an OpenAI-compatible
 * /v1/audio/transcriptions endpoint, return text + segments.
 *
 * This is a slimmed mirror of the production bot's
 * `services/vexa-bot/core/src/services/transcription-client.ts` —
 * same wire contract (multipart form with file/model/response_format),
 * but with no `log` / `utils` dependency so the MVP0 harness stays
 * self-contained. At MVP3 we revisit whether to share the production
 * client directly (extractability audit deliverable).
 */

export interface TranscriptionSegment {
  start: number;
  end: number;
  text: string;
}

export interface TranscriptionResult {
  text: string;
  language: string;
  duration: number;
  segments: TranscriptionSegment[];
}

export class TranscriptionClient {
  private endpoint: string;
  private apiToken?: string;
  private sampleRate: number;
  private timeoutMs: number;

  constructor(opts: { serviceUrl: string; apiToken?: string; sampleRate?: number; timeoutMs?: number }) {
    const base = opts.serviceUrl.replace(/\/+$/, '');
    this.endpoint = base.endsWith('/v1/audio/transcriptions') ? base : `${base}/v1/audio/transcriptions`;
    this.apiToken = opts.apiToken;
    this.sampleRate = opts.sampleRate ?? 16000;
    this.timeoutMs = opts.timeoutMs ?? 30000;
  }

  async transcribe(audio: Float32Array, language?: string): Promise<TranscriptionResult> {
    const wav = float32ToWav(audio, this.sampleRate);
    const boundary = `----RndBoundary${Date.now().toString(36)}`;
    const parts: Buffer[] = [];
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n` +
      `Content-Type: audio/wav\r\n\r\n`,
    ));
    parts.push(wav);
    parts.push(Buffer.from('\r\n'));
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="model"\r\n\r\nwhisper-1\r\n`,
    ));
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="response_format"\r\n\r\nverbose_json\r\n`,
    ));
    if (language) {
      parts.push(Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="language"\r\n\r\n${language}\r\n`,
      ));
    }
    parts.push(Buffer.from(`--${boundary}--\r\n`));
    const body = Buffer.concat(parts);

    const headers: Record<string, string> = {
      'Content-Type': `multipart/form-data; boundary=${boundary}`,
    };
    if (this.apiToken) headers['Authorization'] = `Bearer ${this.apiToken}`;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await fetch(this.endpoint, { method: 'POST', headers, body, signal: controller.signal });
      if (!res.ok) {
        const errText = await res.text().catch(() => '<no body>');
        throw new Error(`transcription HTTP ${res.status}: ${errText.slice(0, 200)}`);
      }
      const data = (await res.json()) as any;
      return {
        text: data.text ?? '',
        language: data.language ?? language ?? 'unknown',
        duration: data.duration ?? 0,
        segments: (data.segments ?? []).map((s: any) => ({
          start: s.start ?? 0,
          end: s.end ?? 0,
          text: s.text ?? '',
        })),
      };
    } finally {
      clearTimeout(timer);
    }
  }
}

function float32ToWav(samples: Float32Array, sampleRate: number): Buffer {
  const numChannels = 1;
  const bitsPerSample = 16;
  const bytesPerSample = bitsPerSample / 8;
  const dataSize = samples.length * bytesPerSample;
  const headerSize = 44;
  const buf = Buffer.alloc(headerSize + dataSize);

  buf.write('RIFF', 0);
  buf.writeUInt32LE(36 + dataSize, 4);
  buf.write('WAVE', 8);
  buf.write('fmt ', 12);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(numChannels, 22);
  buf.writeUInt32LE(sampleRate, 24);
  buf.writeUInt32LE(sampleRate * numChannels * bytesPerSample, 28);
  buf.writeUInt16LE(numChannels * bytesPerSample, 32);
  buf.writeUInt16LE(bitsPerSample, 34);
  buf.write('data', 36);
  buf.writeUInt32LE(dataSize, 40);

  let offset = headerSize;
  for (let i = 0; i < samples.length; i++) {
    let s = samples[i];
    if (s > 1) s = 1; else if (s < -1) s = -1;
    const int16 = s < 0 ? s * 0x8000 : s * 0x7FFF;
    buf.writeInt16LE(Math.round(int16), offset);
    offset += 2;
  }
  return buf;
}

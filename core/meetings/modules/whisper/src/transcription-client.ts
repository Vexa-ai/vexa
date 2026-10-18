import { request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { log } from './log.js';
import { SsrfError, guardedLookup, validateUrl, type Resolver } from './url-guard.js';
import { isLowConfidenceSegment, STRICT_MIN_AUDIO_SEC, type ConfidenceFilterMode } from './confidence.js';
import { encodeFlac } from './flac.js';
import { SttGate, bodySaysOverloaded, parseRetryAfter, type SttPressure } from './stt-gate.js';

export interface TranscriptionWord {
  word: string;
  start: number;
  end: number;
  probability: number;
}

export interface TranscriptionSegment {
  start: number;
  end: number;
  text: string;
  avg_logprob?: number;
  no_speech_prob?: number;
  compression_ratio?: number;
  words?: TranscriptionWord[];
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
  /** Per-request timeout in ms. Default: 90000 (DEFAULT_STT_REQUEST_TIMEOUT_MS). A remote STT answers
   *  a 6 s window in 15–28 s at the tail (measured 2026-10-11 against transcription.vexa.ai), so a
   *  30 s timeout aborted live requests and retried the same audio from scratch. */
  requestTimeoutMs?: number;
  /** Floor of the per-request timeout (ms). Each request's timeout is sized to its window —
   *  max(this, timeoutPerAudioSec × audio seconds), never above `requestTimeoutMs` — so a request
   *  carrying a few seconds is not waited on for the full ceiling. Default 30000 (the measured tail
   *  for a 6 s window is 15–28 s). */
  minRequestTimeoutMs?: number;
  /** Per-request timeout per second of audio (ms of timeout per audio second ÷ 1000). Default 3. */
  timeoutPerAudioSec?: number;
  /** The slowest uplink a request's upload is given time for (bytes/s): the timeout adds
   *  body bytes ÷ this, so a larger body gets proportionally longer. Default 16384 (16 KB/s; a bot
   *  host measured 17–100 KB/s to the remote STT). */
  minUploadBytesPerSec?: number;
  /** Upload encoding. 'flac' (default) is lossless and about half the bytes of 'wav' for speech; a
   *  backend that refuses it is switched to 'wav' once, for this and later requests. */
  audioFormat?: 'flac' | 'wav';
  /** Sample rate of input audio. Default: 16000 */
  sampleRate?: number;
  /** Max speech segment duration in seconds. Whisper forces a segment split at this length.
   *  Lower values = more frequent confirmations = faster output. Default: server default (15s) */
  maxSpeechDurationSec?: number;
  /** Minimum silence duration (ms) for VAD to split segments. Lower = more splits at natural pauses.
   *  Default: server default (160ms). Use ~100ms for more granular segments. */
  minSilenceDurationMs?: number;
  /** STT model id sent as the OpenAI-compatible `model` form part. Backends that validate it
   *  (Groq, vLLM, gateways) need their served name; the bundled unit ignores it (its model is
   *  the unit's own MODEL_SIZE). Default: "whisper-1". */
  model?: string;
  /** The endpoint is the CUSTOMER's (their Settings), not this deployment's: every request is held to
   *  the outbound URL guard — refused when the URL names, or resolves to, an internal or private
   *  address, and dialled at the address that was checked. The deployment's own endpoint (env or
   *  platform setting) may live on a private network and is not held to it. Default: false. */
  publicOnly?: boolean;
  /** Replaces DNS for the guard (tests). */
  resolveHost?: Resolver;
  /** The bot's shared STT gate (in-flight bound, overload pause, circuit breaker). Pass one gate to
   *  every client of one bot; default: a gate of this client's own, built from the options below. */
  gate?: SttGate;
  /** Requests at the STT at once (own gate only). Default 8. */
  maxConcurrent?: number;
  /** Consecutive overload answers that open the breaker (own gate only). Default 3. */
  breakerThreshold?: number;
  /** Overload backoff without Retry-After: first pause and ceiling (ms, own gate only). Defaults 2000 / 30000. */
  overloadBackoffBaseMs?: number;
  overloadBackoffMaxMs?: number;
  /** 'strict' also drops segments Whisper decoded with a low average log-probability (see
   *  confidence.ts for the measured rule); 'off' keeps only the always-on rule. Default 'off'. */
  confidenceFilter?: ConfidenceFilterMode;
}

/** Segment audio RMS below which the STT's text for it is treated as invented over silence. */
const SILENT_SEGMENT_RMS = 0.004;

/** Default per-request STT timeout (ms) — sized to a remote, realtime-tier STT's measured tail. */
export const DEFAULT_STT_REQUEST_TIMEOUT_MS = 90_000;
/** Default floor of a sized per-request timeout (ms). */
export const DEFAULT_STT_MIN_REQUEST_TIMEOUT_MS = 30_000;
/** Default timeout multiple of the window's audio length. */
export const DEFAULT_STT_TIMEOUT_PER_AUDIO_SEC = 3;

/** Default slowest uplink a request is given time for (bytes/s). */
export const DEFAULT_STT_MIN_UPLOAD_BYTES_PER_SEC = 16 * 1024;

/** The timeout one request gets: max(floor, perAudioSec × audio seconds + body bytes ÷ the slowest
 *  uplink), capped at `capMs`. Bigger bodies get proportionally longer; none gets past the cap. */
export function sttRequestTimeoutMs(
  audioSec: number,
  opts: { capMs?: number; minMs?: number; perAudioSec?: number; bodyBytes?: number; minUploadBytesPerSec?: number } = {},
): number {
  const cap = opts.capMs ?? DEFAULT_STT_REQUEST_TIMEOUT_MS;
  const min = opts.minMs ?? DEFAULT_STT_MIN_REQUEST_TIMEOUT_MS;
  const per = opts.perAudioSec ?? DEFAULT_STT_TIMEOUT_PER_AUDIO_SEC;
  const uploadMs = ((opts.bodyBytes ?? 0) / (opts.minUploadBytesPerSec ?? DEFAULT_STT_MIN_UPLOAD_BYTES_PER_SEC)) * 1000;
  return Math.round(Math.min(cap, Math.max(min, per * audioSec * 1000 + uploadMs)));
}

/** The STT boundary's FAILURE vocabulary (P5 + P18: an adapter must translate the
 *  dependency's failures, not just its successes). A consumer reads `.kind` to surface
 *  an attributable health event instead of silently degrading to "no transcript". */
export type TranscriptionFaultKind =
  | 'payment_required'   // 402 — out of balance / credits exhausted
  | 'unauthorized'       // 401 / 403 — bad or expired token
  | 'rate_limited'       // 429
  | 'unavailable'        // 5xx or network error
  | 'timeout'            // request aborted (no response in time)
  | 'bad_request'        // other 4xx
  | 'unknown';

/** A typed STT failure. `source` lets a consumer attribute it; `retryable` drives backoff. */
export class TranscriptionError extends Error {
  readonly source = 'stt' as const;
  constructor(
    readonly kind: TranscriptionFaultKind,
    readonly status: number | undefined,
    readonly detail: string | undefined,
    readonly retryable: boolean,
    /** The STT said it is overloaded (429, 503, or a 5xx relaying an upstream 429/saturation). */
    readonly overloaded: boolean = false,
    /** The STT's Retry-After, in ms, when it gave one. */
    readonly retryAfterMs?: number,
  ) {
    super(`stt ${kind}${status ? ` (HTTP ${status})` : ''}${detail ? `: ${detail}` : ''}`);
    this.name = 'TranscriptionError';
  }
}

/** Map an HTTP status to a typed fault (the anti-corruption translation, P5). */
function classifyHttp(status: number, detail?: string, retryAfterMs?: number): TranscriptionError {
  if (status === 402) return new TranscriptionError('payment_required', status, detail, false);
  if (status === 401 || status === 403) return new TranscriptionError('unauthorized', status, detail, false);
  if (status === 429) return new TranscriptionError('rate_limited', status, detail, true, true, retryAfterMs);
  if (status === 503 || (status >= 500 && bodySaysOverloaded(detail))) {
    return new TranscriptionError('unavailable', status, detail, true, true, retryAfterMs);
  }
  if (status >= 500) return new TranscriptionError('unavailable', status, detail, true);
  if (status >= 400) return new TranscriptionError('bad_request', status, detail, false);
  return new TranscriptionError('unknown', status, detail, false);
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
  private requestTimeoutMs: number;
  private minRequestTimeoutMs: number;
  private timeoutPerAudioSec: number;
  private minUploadBytesPerSec: number;
  private audioFormat: 'flac' | 'wav';
  /** Body bytes of the most recent request (observability and tests). */
  lastBodyBytes = 0;
  /** Monotonic request number — what a timeout line names (no audio, no text). */
  private requestSeq = 0;
  private sampleRate: number;
  private maxSpeechDurationSec: number | undefined;
  private minSilenceDurationMs: number | undefined;
  private model: string;
  private publicOnly: boolean;
  private resolveHost: Resolver | undefined;
  private responseFormat: 'verbose_json' | 'json' = 'verbose_json';
  private confidenceFilter: ConfidenceFilterMode;
  private gate: SttGate;
  constructor(config: TranscriptionClientConfig) {
    // Ensure serviceUrl ends with the transcriptions endpoint
    this.serviceUrl = config.serviceUrl.replace(/\/+$/, '');
    if (!this.serviceUrl.endsWith('/v1/audio/transcriptions')) {
      this.serviceUrl += '/v1/audio/transcriptions';
    }
    this.apiToken = config.apiToken;
    this.maxRetries = config.maxRetries ?? 3;
    this.retryDelayMs = config.retryDelayMs ?? 1000;
    this.requestTimeoutMs = config.requestTimeoutMs ?? DEFAULT_STT_REQUEST_TIMEOUT_MS;
    this.minRequestTimeoutMs = config.minRequestTimeoutMs ?? DEFAULT_STT_MIN_REQUEST_TIMEOUT_MS;
    this.timeoutPerAudioSec = config.timeoutPerAudioSec ?? DEFAULT_STT_TIMEOUT_PER_AUDIO_SEC;
    this.minUploadBytesPerSec = config.minUploadBytesPerSec ?? DEFAULT_STT_MIN_UPLOAD_BYTES_PER_SEC;
    this.audioFormat = config.audioFormat ?? 'flac';
    this.sampleRate = config.sampleRate ?? 16000;
    this.maxSpeechDurationSec = config.maxSpeechDurationSec;
    this.minSilenceDurationMs = config.minSilenceDurationMs;
    this.model = config.model ?? 'whisper-1';
    this.confidenceFilter = config.confidenceFilter ?? 'off';
    this.publicOnly = config.publicOnly ?? false;
    this.resolveHost = config.resolveHost;
    this.gate = config.gate ?? new SttGate({
      maxConcurrent: config.maxConcurrent, breakerThreshold: config.breakerThreshold,
      backoffBaseMs: config.overloadBackoffBaseMs, backoffMaxMs: config.overloadBackoffMaxMs,
    });
  }

  /** The STT back-pressure this client's requests pass through (shared per bot). */
  pressure(): SttPressure {
    return this.gate;
  }

  /**
   * Transcribe a Float32Array audio buffer.
   * Converts to WAV, POSTs to transcription-service, returns parsed result.
   * Retries on transient failures (503, network errors).
   */
  async transcribe(audioData: Float32Array, language?: string, prompt?: string): Promise<TranscriptionResult> {
    const audioSec = audioData.length / this.sampleRate;
    const requestId = ++this.requestSeq;
    let format = this.audioFormat;
    let body = this.encode(audioData, format);
    let timeoutMs = this.timeoutFor(audioSec, body.length);

    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        if (format !== this.audioFormat) {         // negotiated down to WAV by an earlier request
          format = this.audioFormat;
          body = this.encode(audioData, format);
          timeoutMs = this.timeoutFor(audioSec, body.length);
        }
        this.lastBodyBytes = body.length;
        // Every request waits its turn at the bot's shared gate: bounded in flight, held while the
        // STT is overloaded, released in arrival order.
        const release = await this.gate.acquire();
        let result: TranscriptionResult;
        try {
          result = await this.sendRequest(body, language, prompt, timeoutMs, format, audioSec);
        } finally {
          release();
        }
        this.gate.onSuccess();
        return this.dropSilentSegments(result, audioData);
      } catch (err: any) {
        // Normalize anything non-HTTP (abort/network) into a typed fault too, so the
        // thrown value is ALWAYS a TranscriptionError the consumer can attribute (P18).
        const fault: TranscriptionError = err instanceof TranscriptionError
          ? err
          : new TranscriptionError(err?.name === 'AbortError' ? 'timeout' : 'unavailable', undefined, err?.message, true);
        if (fault.kind === 'timeout') {
          // One attributable line per timed-out attempt: which request, how much audio, what limit.
          log(`[TranscriptionClient] [STT-TIMEOUT] request ${requestId} (${audioSec.toFixed(1)}s audio) ` +
              `exceeded ${timeoutMs}ms on attempt ${attempt + 1}/${this.maxRetries + 1}`);
        }
        if (fault.kind === 'bad_request' && this.responseFormat === 'verbose_json' && /verbose_json/i.test(fault.detail ?? '')) {
          log('[TranscriptionClient] backend rejected verbose_json; falling back to json for this and later requests');
          this.responseFormat = 'json';
          attempt--; // Capability negotiation does not consume a configured retry.
          continue;
        }
        if (fault.kind === 'bad_request' && format === 'flac' && /flac|format|decode|unsupported|invalid (audio|file)/i.test(fault.detail ?? '')) {
          log('[TranscriptionClient] backend rejected FLAC; sending WAV for this and later requests');
          this.audioFormat = 'wav';
          attempt--;
          continue;
        }
        const isLastAttempt = attempt === this.maxRetries;

        if (fault.overloaded) {
          // Overload is the STT's state, not this request's: the shared gate pauses every submission
          // (Retry-After, else jittered exponential backoff) — no per-request retry schedule.
          const pause = this.gate.onOverload(fault.retryAfterMs);
          if (!isLastAttempt) {
            log(`[TranscriptionClient] [STT-OVERLOAD] ${fault.message} (attempt ${attempt + 1}/${this.maxRetries + 1}) — ` +
                `all submissions paused ${pause}ms${fault.retryAfterMs !== undefined ? ' (Retry-After)' : ''}`);
            continue;
          }
        } else {
          this.gate.onOtherFailure();
        }

        if (fault.retryable && !isLastAttempt && !fault.overloaded) {
          const delay = this.retryDelayMs * Math.pow(2, attempt);
          log(`[TranscriptionClient] ${fault.kind} (attempt ${attempt + 1}/${this.maxRetries + 1}): ${fault.message}. Retrying in ${delay}ms...`);
          await new Promise(resolve => setTimeout(resolve, delay));
          continue;
        }

        // Non-retryable (402/401/4xx) or retries exhausted → surface the typed fault.
        log(`[TranscriptionClient] transcription failed after ${attempt + 1} attempt(s): ${fault.message}`);
        throw fault;
      }
    }

    // Should never reach here, but TypeScript needs it
    throw new Error('Transcription failed: exhausted retries');
  }

  /** POST through the outbound URL guard: the URL is checked as written, the socket is dialled at
   *  the address the connect-time lookup checked, and a redirect is an answer, not a hop. A refusal
   *  is a non-retryable `bad_request` fault naming why. */
  private async guardedPost(headers: Record<string, string>, body: Buffer, signal: AbortSignal):
      Promise<{ ok: boolean; status: number; headers: { get(name: string): string | null };
        text(): Promise<string>; json(): Promise<unknown> }> {
    const refused = (e: Error) => new TranscriptionError('bad_request', undefined,
      `customer endpoint refused: ${e.message}`, false);
    try {
      await validateUrl(this.serviceUrl, { resolve: false, what: 'transcription endpoint' });
    } catch (e) {
      throw e instanceof SsrfError ? refused(e) : e;
    }
    const url = new URL(this.serviceUrl);
    const send = url.protocol === 'https:' ? httpsRequest : httpRequest;
    return await new Promise((resolve, reject) => {
      const req = send(url, {
        method: 'POST', headers: { ...headers, 'Content-Length': String(body.length) }, signal,
        lookup: guardedLookup(this.resolveHost) as any,
      }, (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (c: Buffer) => chunks.push(c));
        res.on('error', reject);
        res.on('end', () => {
          const raw = Buffer.concat(chunks).toString('utf8');
          const status = res.statusCode ?? 0;
          const headers = { get: (name: string) => {
            const v = res.headers[name.toLowerCase()];
            return v === undefined ? null : Array.isArray(v) ? v.join(', ') : String(v);
          } };
          resolve({ ok: status >= 200 && status < 300, status, headers,
                    text: async () => raw, json: async () => JSON.parse(raw) });
        });
      });
      req.on('error', (e: NodeJS.ErrnoException) =>
        reject(e.code === 'EVEXAREFUSED' ? refused(e) : e));
      req.end(body);
    });
  }

  /**
   * Send the WAV buffer to the transcription-service as multipart form data.
   */
  /** Drop segments the STT placed over near-silence (the "Thank you." / "Продолжение следует…" it
   *  invents at the end of a window), checked against the audio itself, and rebuild the text. */
  private dropSilentSegments(result: TranscriptionResult, audio: Float32Array): TranscriptionResult {
    if (!result.segments.length) return result;
    const kept = result.segments.filter((seg) => {
      const a = Math.max(0, Math.floor(seg.start * this.sampleRate));
      const b = Math.min(audio.length, Math.ceil(seg.end * this.sampleRate));
      if (b <= a) return true;                    // no usable span: keep, judge elsewhere
      let sum = 0;
      for (let i = a; i < b; i++) sum += audio[i] * audio[i];
      return Math.sqrt(sum / (b - a)) >= SILENT_SEGMENT_RMS;
    });
    if (kept.length === result.segments.length) return result;
    log(`[STT] dropped ${result.segments.length - kept.length}/${result.segments.length} segment(s) placed over silence`);
    return { ...result, segments: kept, text: kept.map((s) => s.text.trim()).filter(Boolean).join(' ') };
  }

  private encode(audio: Float32Array, format: 'flac' | 'wav'): Buffer {
    return format === 'flac' ? Buffer.from(encodeFlac(audio, this.sampleRate)) : this.float32ToWav(audio);
  }

  private timeoutFor(audioSec: number, bodyBytes: number): number {
    return sttRequestTimeoutMs(audioSec, {
      capMs: this.requestTimeoutMs, minMs: this.minRequestTimeoutMs, perAudioSec: this.timeoutPerAudioSec,
      bodyBytes, minUploadBytesPerSec: this.minUploadBytesPerSec,
    });
  }

  private async sendRequest(audioBody: Buffer, language?: string, prompt?: string,
      timeoutMs: number = this.requestTimeoutMs, format: 'flac' | 'wav' = 'wav', audioSec = Infinity): Promise<TranscriptionResult> {
    // Strict applies only to windows as long as the ones it was measured on: a short window's
    // average log-probability is lower for real speech too.
    const filterMode = audioSec >= STRICT_MIN_AUDIO_SEC ? this.confidenceFilter : 'off';
    // Build multipart form data manually (no external dependency needed)
    const boundary = `----FormBoundary${Date.now().toString(36)}`;

    const parts: Buffer[] = [];

    // File part
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="file"; filename="audio.${format}"\r\n` +
      `Content-Type: audio/${format}\r\n\r\n`
    ));
    parts.push(audioBody);
    parts.push(Buffer.from('\r\n'));

    // Model part (required by OpenAI-compatible API; validating backends reject unknown ids)
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="model"\r\n\r\n` +
      `${this.model}\r\n`
    ));

    // Response format part
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="response_format"\r\n\r\n` +
      `${this.responseFormat}\r\n`
    ));

    // Language part (if specified)
    if (language) {
      parts.push(Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="language"\r\n\r\n` +
        `${language}\r\n`
      ));
    }

    // Request word-level timestamps
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="timestamp_granularities"\r\n\r\n` +
      `word\r\n`
    ));

    // Max speech segment duration (controls how often Whisper splits segments)
    if (this.maxSpeechDurationSec !== undefined) {
      parts.push(Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="max_speech_duration_s"\r\n\r\n` +
        `${this.maxSpeechDurationSec}\r\n`
      ));
    }

    // Min silence duration for VAD segment splitting (lower = more splits at natural pauses)
    if (this.minSilenceDurationMs !== undefined) {
      parts.push(Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="min_silence_duration_ms"\r\n\r\n` +
        `${this.minSilenceDurationMs}\r\n`
      ));
    }

    // Prompt: previous confirmed text as context for streaming continuity
    if (prompt) {
      parts.push(Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="prompt"\r\n\r\n` +
        `${prompt}\r\n`
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
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = this.publicOnly
        ? await this.guardedPost(headers, body, controller.signal)
        : await fetch(this.serviceUrl, {
          method: 'POST',
          headers,
          body,
          signal: controller.signal,
        });

      if (!response.ok) {
        const errorText = await response.text().catch(() => 'Unable to read error response');
        const retryAfterMs = parseRetryAfter(response.headers?.get?.('retry-after'));
        throw classifyHttp(response.status, errorText, retryAfterMs);   // typed fault (P5/P18), not a bare Error
      }

      const data = await response.json() as any;

      const allSegments = (data.segments || []).map((s: any) => ({
        start: s.start || 0,
        end: s.end || 0,
        text: s.text || '',
        avg_logprob: s.avg_logprob,
        no_speech_prob: s.no_speech_prob,
        compression_ratio: s.compression_ratio,
        words: s.words,
      }));
      // Drop low-confidence (hallucinated / faint-bleed) segments at the source and
      // rebuild the text from what survives, so phantoms never reach the pipeline.
      // If the model returned no segments we can't score, so keep its text as-is.
      const segments = allSegments.filter((s: any) => !isLowConfidenceSegment(s, filterMode));
      const text = allSegments.length
        ? segments.map((s: any) => (s.text || '').trim()).filter(Boolean).join(' ')
        : (data.text || '');
      if (allSegments.length && segments.length < allSegments.length) {
        log(`[STT] dropped ${allSegments.length - segments.length}/${allSegments.length} low-confidence segment(s)`);
        if (filterMode === 'strict') {
          const dropped = allSegments.filter((s: any) => !segments.includes(s));
          log(`[STT-CONFIDENCE] strict: dropped ${dropped.map((s: any) =>
            `${(+s.start).toFixed(1)}-${(+s.end).toFixed(1)}s avg_logprob=${s.avg_logprob?.toFixed?.(2)} no_speech=${s.no_speech_prob?.toFixed?.(2)}`).join('; ')}`);
        }
      }
      return {
        text,
        language: data.language || language || 'unknown',
        language_probability: data.language_probability ?? 0,
        duration: data.duration || 0,
        segments,
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

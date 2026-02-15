import { spawn, ChildProcess } from 'child_process';
import { Readable } from 'stream';
import { log } from '../utils';
import https from 'https';
import http from 'http';

/**
 * TTSPlaybackService
 *
 * Plays audio into the meeting via PulseAudio tts_sink.
 * Supports:
 *   - Raw PCM playback (from external agents with their own TTS)
 *   - WAV/MP3 file playback
 *   - Text-to-speech via OpenAI, Cartesia, or ElevenLabs APIs
 *   - Audio from URL (fetch + play)
 *   - Base64-encoded audio
 *   - Barge-in interruption
 */
export class TTSPlaybackService {
  private paplayProcess: ChildProcess | null = null;
  private _isPlaying: boolean = false;
  private _currentText: string | null = null;

  /**
   * Play raw PCM audio through PulseAudio tts_sink.
   * @param pcmData Raw PCM buffer (Int16LE by default)
   * @param sampleRate Sample rate in Hz (default 24000 for most TTS)
   * @param channels Number of channels (default 1 = mono)
   * @param format PulseAudio format string (default s16le)
   */
  async playPCM(
    pcmData: Buffer,
    sampleRate: number = 24000,
    channels: number = 1,
    format: string = 's16le'
  ): Promise<void> {
    this._isPlaying = true;
    return new Promise((resolve, reject) => {
      const proc = spawn('paplay', [
        '--raw',
        `--format=${format}`,
        `--rate=${sampleRate}`,
        `--channels=${channels}`,
        '--device=tts_sink'
      ], { stdio: ['pipe', 'pipe', 'pipe'] });

      this.paplayProcess = proc;

      proc.stderr?.on('data', (data: Buffer) => {
        log(`[TTS Playback] paplay stderr: ${data.toString().trim()}`);
      });

      proc.on('exit', (code) => {
        this._isPlaying = false;
        this.paplayProcess = null;
        if (code === 0 || code === null) {
          resolve();
        } else {
          reject(new Error(`paplay exited with code ${code}`));
        }
      });

      proc.on('error', (err) => {
        this._isPlaying = false;
        this.paplayProcess = null;
        reject(err);
      });

      // Write all data and close stdin
      proc.stdin?.write(pcmData, () => {
        proc.stdin?.end();
      });
    });
  }

  /**
   * Stream raw PCM data to paplay as it arrives (for streaming TTS).
   * Returns a writable interface — call write() for each chunk, end() when done.
   */
  startPCMStream(
    sampleRate: number = 24000,
    channels: number = 1,
    format: string = 's16le'
  ): { write: (chunk: Buffer) => boolean; end: () => void; onDone: Promise<void> } {
    this._isPlaying = true;

    const proc = spawn('paplay', [
      '--raw',
      `--format=${format}`,
      `--rate=${sampleRate}`,
      `--channels=${channels}`,
      '--device=tts_sink'
    ], { stdio: ['pipe', 'pipe', 'pipe'] });

    this.paplayProcess = proc;

    proc.stderr?.on('data', (data: Buffer) => {
      log(`[TTS Playback] paplay stderr: ${data.toString().trim()}`);
    });

    const onDone = new Promise<void>((resolve, reject) => {
      proc.on('exit', (code) => {
        this._isPlaying = false;
        this.paplayProcess = null;
        if (code === 0 || code === null) resolve();
        else reject(new Error(`paplay stream exited with code ${code}`));
      });
      proc.on('error', (err) => {
        this._isPlaying = false;
        this.paplayProcess = null;
        reject(err);
      });
    });

    return {
      write: (chunk: Buffer) => {
        if (proc.stdin && !proc.stdin.destroyed) {
          return proc.stdin.write(chunk);
        }
        return false;
      },
      end: () => {
        if (proc.stdin && !proc.stdin.destroyed) {
          proc.stdin.end();
        }
      },
      onDone
    };
  }

  /**
   * Play a WAV or MP3 file through PulseAudio tts_sink.
   * Uses ffmpeg to decode to raw PCM, piped into paplay.
   */
  async playFile(filePath: string): Promise<void> {
    this._isPlaying = true;
    return new Promise((resolve, reject) => {
      // Use ffmpeg to convert any audio format to raw PCM, pipe to paplay
      const ffmpeg = spawn('ffmpeg', [
        '-i', filePath,
        '-f', 's16le',
        '-acodec', 'pcm_s16le',
        '-ar', '24000',
        '-ac', '1',
        '-loglevel', 'error',
        'pipe:1'
      ], { stdio: ['pipe', 'pipe', 'pipe'] });

      const paplay = spawn('paplay', [
        '--raw',
        '--format=s16le',
        '--rate=24000',
        '--channels=1',
        '--device=tts_sink'
      ], { stdio: ['pipe', 'pipe', 'pipe'] });

      this.paplayProcess = paplay;

      // Pipe ffmpeg output to paplay input
      ffmpeg.stdout?.pipe(paplay.stdin!);

      ffmpeg.stderr?.on('data', (data: Buffer) => {
        log(`[TTS Playback] ffmpeg stderr: ${data.toString().trim()}`);
      });

      paplay.stderr?.on('data', (data: Buffer) => {
        log(`[TTS Playback] paplay stderr: ${data.toString().trim()}`);
      });

      paplay.on('exit', (code) => {
        this._isPlaying = false;
        this.paplayProcess = null;
        if (code === 0 || code === null) resolve();
        else reject(new Error(`paplay exited with code ${code}`));
      });

      paplay.on('error', (err) => {
        this._isPlaying = false;
        this.paplayProcess = null;
        ffmpeg.kill('SIGTERM');
        reject(err);
      });

      ffmpeg.on('error', (err) => {
        this._isPlaying = false;
        paplay.kill('SIGTERM');
        reject(err);
      });
    });
  }

  /**
   * Play audio from a URL (fetches the audio, decodes via ffmpeg, plays via paplay).
   */
  async playFromUrl(url: string): Promise<void> {
    this._isPlaying = true;
    return new Promise((resolve, reject) => {
      const ffmpeg = spawn('ffmpeg', [
        '-i', url,
        '-f', 's16le',
        '-acodec', 'pcm_s16le',
        '-ar', '24000',
        '-ac', '1',
        '-loglevel', 'error',
        'pipe:1'
      ], { stdio: ['pipe', 'pipe', 'pipe'] });

      const paplay = spawn('paplay', [
        '--raw',
        '--format=s16le',
        '--rate=24000',
        '--channels=1',
        '--device=tts_sink'
      ], { stdio: ['pipe', 'pipe', 'pipe'] });

      this.paplayProcess = paplay;
      ffmpeg.stdout?.pipe(paplay.stdin!);

      ffmpeg.stderr?.on('data', (data: Buffer) => {
        log(`[TTS Playback] ffmpeg stderr: ${data.toString().trim()}`);
      });
      paplay.stderr?.on('data', (data: Buffer) => {
        log(`[TTS Playback] paplay stderr: ${data.toString().trim()}`);
      });

      paplay.on('exit', (code) => {
        this._isPlaying = false;
        this.paplayProcess = null;
        if (code === 0 || code === null) resolve();
        else reject(new Error(`paplay exited with code ${code}`));
      });

      paplay.on('error', (err) => {
        this._isPlaying = false;
        this.paplayProcess = null;
        ffmpeg.kill('SIGTERM');
        reject(err);
      });

      ffmpeg.on('error', (err) => {
        this._isPlaying = false;
        paplay.kill('SIGTERM');
        reject(err);
      });
    });
  }

  /**
   * Play audio from base64-encoded data.
   * @param base64Data Base64-encoded audio (WAV, MP3, PCM, etc.)
   * @param format Audio format hint: 'wav', 'mp3', 'pcm', 'opus'
   * @param sampleRate For PCM format, the sample rate (default 24000)
   */
  async playFromBase64(base64Data: string, format: string = 'wav', sampleRate: number = 24000): Promise<void> {
    const buffer = Buffer.from(base64Data, 'base64');

    if (format === 'pcm') {
      // Raw PCM — play directly
      return this.playPCM(buffer, sampleRate, 1, 's16le');
    }

    // For WAV/MP3/etc, write to temp file and use playFile
    const fs = await import('fs');
    const path = await import('path');
    const tmpPath = path.join('/tmp', `tts-${Date.now()}.${format}`);
    fs.writeFileSync(tmpPath, buffer);
    try {
      await this.playFile(tmpPath);
    } finally {
      try { fs.unlinkSync(tmpPath); } catch {}
    }
  }

  /**
   * Synthesize text to speech via the Vexa TTS service and play it.
   * Streams the audio for low latency.
   */
  async synthesizeAndPlay(
    text: string,
    provider: string = 'openai',
    voice: string = 'alloy'
  ): Promise<void> {
    this._currentText = text;
    log(`[TTS] Synthesizing with ${provider}, voice=${voice}: "${text.substring(0, 50)}..."`);
    await this.synthesizeViaTtsService(text, voice);
    this._currentText = null;
  }

  /**
   * TTS synthesis via Vexa TTS service. Streams response audio directly to paplay.
   */
  private async synthesizeViaTtsService(text: string, voice: string): Promise<void> {
    const ttsServiceUrl = process.env.TTS_SERVICE_URL?.trim();
    if (!ttsServiceUrl) {
      throw new Error('[TTS] TTS_SERVICE_URL not set');
    }

    this._isPlaying = true;

    const postData = JSON.stringify({
      model: 'tts-1',
      input: text,
      voice: voice,
      response_format: 'pcm' // Raw PCM Int16LE 24kHz mono
    });

    const base = ttsServiceUrl.replace(/\/$/, '');
    const url = new URL(`${base}/v1/audio/speech`);
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'Content-Length': String(Buffer.byteLength(postData))
    };
    const ttsToken = process.env.TTS_API_TOKEN?.trim();
    if (ttsToken) headers['X-API-Key'] = ttsToken;

    return new Promise((resolve, reject) => {
      const req = (url.protocol === 'https:' ? https : http).request({
        hostname: url.hostname,
        port: url.port || (url.protocol === 'https:' ? 443 : 80),
        path: url.pathname + url.search,
        method: 'POST',
        headers
      }, (res) => {
        if (res.statusCode !== 200) {
          let body = '';
          res.on('data', (chunk) => body += chunk);
          res.on('end', () => {
            this._isPlaying = false;
            reject(new Error(`TTS service error ${res.statusCode}: ${body}`));
          });
          return;
        }

        // Stream response directly to paplay (OpenAI pcm format = Int16LE 24kHz mono)
        const paplay = spawn('paplay', [
          '--raw',
          '--format=s16le',
          '--rate=24000',
          '--channels=1',
          '--device=tts_sink'
        ], { stdio: ['pipe', 'pipe', 'pipe'] });

        this.paplayProcess = paplay;

        paplay.stderr?.on('data', (data: Buffer) => {
          log(`[TTS Playback] paplay stderr: ${data.toString().trim()}`);
        });

        paplay.on('exit', (code) => {
          this._isPlaying = false;
          this.paplayProcess = null;
          this._currentText = null;
          if (code === 0 || code === null) resolve();
          else reject(new Error(`paplay exited with code ${code}`));
        });

        paplay.on('error', (err) => {
          this._isPlaying = false;
          this.paplayProcess = null;
          reject(err);
        });

        // Pipe HTTP response body directly to paplay stdin
        res.pipe(paplay.stdin!);
      });

      req.on('error', (err) => {
        this._isPlaying = false;
        reject(err);
      });

      req.write(postData);
      req.end();
    });
  }

  /**
   * Interrupt current playback immediately (for barge-in support).
   */
  interrupt(): void {
    if (this.paplayProcess) {
      log('[TTS Playback] Interrupting playback (barge-in)');
      try {
        this.paplayProcess.stdin?.destroy();
        this.paplayProcess.kill('SIGKILL');
      } catch {}
      this.paplayProcess = null;
    }
    this._isPlaying = false;
    this._currentText = null;
  }

  /**
   * Stop playback gracefully.
   */
  stop(): void {
    if (this.paplayProcess) {
      log('[TTS Playback] Stopping playback');
      try {
        this.paplayProcess.stdin?.end();
        this.paplayProcess.kill('SIGTERM');
      } catch {}
      this.paplayProcess = null;
    }
    this._isPlaying = false;
    this._currentText = null;
  }

  /**
   * Check if audio is currently playing.
   */
  isPlaying(): boolean {
    return this._isPlaying;
  }

  /**
   * Get the text currently being spoken (null if not speaking).
   */
  getCurrentText(): string | null {
    return this._currentText;
  }
}

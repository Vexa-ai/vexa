/**
 * TTS playback adapter (2b) — the OS-level half of the SPEAK path.  // L4 (O6/VM).
 *
 * The browser half (the meeting-UI mic, read before it is clicked) and the queue live in
 * speak-controller.ts; this is the audio half: synthesize `text` with the deployment's
 * OpenAI-compatible TTS and play the returned PCM through the container's PulseAudio `tts_sink`
 * (→ `virtual_mic`, which Chromium captures as its mic). The `tts_sink → virtual_mic` graph is
 * created by entrypoint.sh (and Lite's vexa-bot-launch); here we only unmute `tts_sink` during
 * playback, stream PCM to `paplay`, and re-mute it after. `virtual_mic` itself is never muted: Meet
 * shows a muted source as "Microphone problem" in place of the mic toggle, and a muted tts_sink
 * already feeds it silence (and keeps the browser's own audio, which also lands in tts_sink, out — #819).
 *
 * Config is infrastructure, read from env (forwarded to every spawned bot by the runtime's
 * meeting-bot profile, #1866 C6), NOT the sealed invocation.v1:
 *   TTS_SERVICE_URL — the base; we POST {base}/v1/audio/speech with response_format "pcm" and play
 *                     the answer as 24 kHz s16le mono.
 *   TTS_API_TOKEN   — sent as `Authorization: Bearer` (OpenAI) AND `X-API-Key` (Vexa-style services).
 *   TTS_VOICE       — the voice when a speak names none or names "auto"; unset → `alloy`.
 *
 * `play` returns a typed outcome instead of only logging, so the speak queue names the reason on
 * the utterance's own line. The HTTP client, the player and the PulseAudio mute are injectable ports,
 * so tts-playback.test.ts needs no network and no PulseAudio. Exactly one player runs at a time.
 * Nothing here logs the spoken text or the token.
 *
 * Node-only (child_process + http/https) — no DOM, no workspace imports → gate:isolation-clean.
 */
import { spawn, execSync } from 'node:child_process';
import https from 'node:https';
import http from 'node:http';
import type { Readable, Writable } from 'node:stream';
import type { TtsOutcome, TtsPort } from './speak-controller.js';

const PAPLAY_ARGS = ['--raw', '--format=s16le', '--rate=24000', '--channels=1', '--device=tts_sink'];
const DEFAULT_VOICE = 'alloy';

export const pactlMuteCommand = (muted: boolean): string => `pactl set-sink-mute tts_sink ${muted ? '1' : '0'}`;

/** POSTs the synthesis request; resolves with the status and the (streamed) body. Rejects when the
 *  server cannot be reached. */
export type TtsHttp = (url: URL, init: { headers: Record<string, string>; body: string }) =>
  Promise<{ status: number; body: Readable }>;

/** A started `paplay`: PCM in on stdin, `exit` when it has played (or was killed). */
export interface TtsPlayer {
  stdin: Writable | null;
  kill(): void;
  on(event: 'exit', cb: (code: number | null) => void): unknown;
  on(event: 'error', cb: (e: Error) => void): unknown;
}

export interface TtsPlaybackPorts {
  request?: TtsHttp;
  player?: () => TtsPlayer;
  setMute?: (muted: boolean) => void;
  env?: Record<string, string | undefined>;
  now?: () => number;
}

/** The TTS half the speak queue drives (`TtsPort`). */
export type TtsPlayback = TtsPort;

const nodeHttp: TtsHttp = (url, init) => new Promise((resolve, reject) => {
  const req = (url.protocol === 'https:' ? https : http).request({
    hostname: url.hostname,
    port: url.port || (url.protocol === 'https:' ? 443 : 80),
    path: url.pathname + url.search,
    method: 'POST',
    headers: { ...init.headers, 'Content-Length': String(Buffer.byteLength(init.body)) },
  }, (res) => resolve({ status: res.statusCode ?? 0, body: res }));
  req.on('error', reject);
  req.write(init.body);
  req.end();
});

const paplay = (): TtsPlayer => spawn('paplay', PAPLAY_ARGS, { stdio: ['pipe', 'ignore', 'pipe'] }) as unknown as TtsPlayer;

export function createTtsPlayback(log: (m: string) => void = () => { /* */ }, ports: TtsPlaybackPorts = {}): TtsPlayback {
  const env = ports.env ?? process.env;
  const request = ports.request ?? nodeHttp;
  const startPlayer = ports.player ?? paplay;
  const now = ports.now ?? Date.now;
  const setMute = ports.setMute ?? ((muted: boolean): void => {
    try {
      execSync(pactlMuteCommand(muted), { stdio: 'pipe' });
    } catch (err) {
      log(`[tts] pactl ${muted ? 'mute' : 'unmute'} failed: ${(err as Error).message}`);
    }
  });

  let proc: TtsPlayer | null = null;
  // The TTS answer being played. Closed on stop, so the server stops synthesizing what nobody will hear.
  let audio: Readable | null = null;
  // Settles the utterance in flight; `stop()` settles it as `stopped`.
  let settle: ((o: TtsOutcome) => void) | null = null;

  const kill = (): void => {
    const p = proc;
    proc = null;
    audio?.destroy();
    audio = null;
    if (p) { try { p.stdin?.destroy(); p.kill(); } catch { /* already gone */ } }
  };

  const stop = (): void => {
    const s = settle;
    settle = null;
    kill();
    setMute(true);
    s?.({ ok: false, reason: 'stopped' });
  };

  const play = async (text: string, voice?: string): Promise<TtsOutcome> => {
    // One player at a time: whatever was playing ends here (the queue never asks for two).
    if (settle || proc) stop();
    const base = env.TTS_SERVICE_URL?.trim();
    if (!base) return { ok: false, reason: 'tts_not_configured' };
    let url: URL;
    try { url = new URL(`${base.replace(/\/+$/, '')}/v1/audio/speech`); }
    catch { return { ok: false, reason: 'tts_not_configured' }; }
    const chosen = voice && voice !== 'auto' ? voice : (env.TTS_VOICE?.trim() || DEFAULT_VOICE);
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const token = env.TTS_API_TOKEN?.trim();
    if (token) { headers['Authorization'] = `Bearer ${token}`; headers['X-API-Key'] = token; }
    const body = JSON.stringify({ model: 'tts-1', input: text, voice: chosen, response_format: 'pcm' });

    return new Promise<TtsOutcome>((resolve) => {
      let finished = false;
      const finish = (o: TtsOutcome) => { if (finished) return; finished = true; if (settle === finish) settle = null; resolve(o); };
      settle = finish;
      request(url, { headers, body }).then((res) => {
        if (finished) { res.body.destroy(); return; }
        if (res.status !== 200) {
          res.body.resume?.();
          log(`[tts] service answered ${res.status}`);
          finish({ ok: false, reason: `tts_http_${res.status}` });
          return;
        }
        const started = now();
        setMute(false);                                   // open the mic only during playback
        const p = startPlayer();
        proc = p;
        audio = res.body;
        const done = (code: number | null) => {
          if (proc === p) { proc = null; audio = null; }
          setMute(true);
          finish(code === 0 ? { ok: true, ms: Math.max(1, now() - started) } : { ok: false, reason: 'playback_failed' });
        };
        p.on('exit', done);
        p.on('error', (e) => { log(`[tts] paplay error: ${e.message}`); done(1); });
        if (p.stdin) res.body.pipe(p.stdin);              // stream PCM straight to the mic sink
      }, (e: Error) => {
        log(`[tts] service unreachable: ${e.message}`);
        finish({ ok: false, reason: 'tts_unreachable' });
      });
    });
  };

  return { play, stop };
}

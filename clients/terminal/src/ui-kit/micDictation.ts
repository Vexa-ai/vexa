/** micDictation — the composer mic: words stream into the text box while the person speaks.
 *
 *  The browser half only: capture 16 kHz mono from the microphone, feed it to the dictation engine
 *  (ui-kit/dictationEngine — drafts every half second, a final at each pause), and upload each window
 *  to /api/stt as lossless FLAC. Transcription stays server-side, through the deployment's own STT
 *  route; no meeting or voice-note record is created and nothing is sent to the chat.
 *
 *  Duplicated rather than imported because the terminal image is a self-contained npm build (no
 *  @vexa/* workspace deps — see clients/terminal/Dockerfile).
 */
import { createDictationEngine, type DictationEngineOptions } from "./dictationEngine";
import { encodeFlac } from "./flac";

export interface SttWord { word: string; start: number; end: number }

export interface StreamingDictation {
  /** Stop capturing, finalize the last window, and resolve the FULL final text. */
  stop(): Promise<string>;
  /** Abort immediately: release the mic, discard everything. */
  cancel(): void;
  /** The shown text left the composer (sent or edited): drop late answers for audio so far. */
  commit(): void;
}

export interface StreamingDictationOptions {
  /** Live text: final text so far + the current draft (replaced in place as it firms up). */
  onUpdate?: (finalText: string, draft: string) => void;
  /** Input level 0..1 of the last frame, for a meter. */
  onLevel?: (level: number) => void;
  /** Non-fatal mid-stream STT failures (the window is retried). */
  onError?: (message: string) => void;
  /** Optional language hint (ISO code); absent = the STT detects it. */
  language?: string;
  /** Engine tuning (tests, experiments). */
  engine?: Partial<DictationEngineOptions>;
}

const SAMPLE_RATE = 16000;

/** A microphone failure in words a person can act on. */
export function micErrorMessage(e: unknown): string {
  const name = (e as { name?: string } | null)?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError") return "Microphone access is blocked. Allow the microphone for this site in your browser settings, then try again.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No microphone was found. Connect one and try again.";
  if (name === "NotReadableError" || name === "AbortError") return "The microphone is busy in another app. Close it there and try again.";
  if (typeof navigator !== "undefined" && !navigator.mediaDevices?.getUserMedia) return "This browser can't record audio here. Open the page over https, or try another browser.";
  return "The microphone couldn't start. Try again.";
}

/** POST one window to /api/stt as FLAC; `final` marks the window's last pass. */
export async function transcribeWindow(pcm: Float32Array, prompt: string, language?: string): Promise<{ text: string; words: SttWord[] }> {
  const q = new URLSearchParams();
  if (prompt) q.set("prompt", prompt);
  if (language) q.set("language", language);
  const qs = q.toString();
  const r = await fetch(`/api/stt${qs ? `?${qs}` : ""}`, { method: "POST", headers: { "Content-Type": "audio/flac" }, body: encodeFlac(pcm, SAMPLE_RATE).buffer as ArrayBuffer });
  const d = (await r.json().catch(() => ({}))) as { text?: string; words?: SttWord[]; error?: string };
  if (!r.ok) throw new Error(d.error || `transcription failed (${r.status})`);
  return { text: (d.text ?? "").trim(), words: d.words ?? [] };
}

export async function startStreamingDictation(opts: StreamingDictationOptions = {}): Promise<StreamingDictation> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
  const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
  const source = ctx.createMediaStreamSource(stream);
  // 1024 frames ≈ 64 ms at 16 kHz: a short frame so the pause detector and the meter are prompt.
  const proc = ctx.createScriptProcessor(1024, 1, 1);
  let cancelled = false;   // after cancel, answers still in flight must not write into the box
  const engine = createDictationEngine({
    transcribe: (pcm, prompt) => transcribeWindow(pcm, prompt, opts.language),
    onUpdate: (f, d) => { if (!cancelled) opts.onUpdate?.(f, d); },
    onError: (m) => opts.onError?.(m),
    sampleRate: SAMPLE_RATE,
    ...opts.engine,
  });
  proc.onaudioprocess = (e: AudioProcessingEvent) => {
    const level = engine.feed(new Float32Array(e.inputBuffer.getChannelData(0)));  // copy — the buffer is reused
    opts.onLevel?.(Math.min(1, level * 8));
  };
  source.connect(proc);
  proc.connect(ctx.destination);  // pull the processor (outputs silence)
  void ctx.resume().catch(() => { /* best-effort */ });
  const timer = setInterval(() => engine.tick(), 100);   // the engine paces drafts itself; this only bounds pause detection

  const release = () => {
    clearInterval(timer);
    try { proc.disconnect(); proc.onaudioprocess = null; } catch { /* */ }
    stream.getTracks().forEach((t) => t.stop());
    void ctx.close().catch(() => { /* */ });
  };
  return {
    async stop() { release(); return engine.stop(); },
    cancel() { cancelled = true; release(); },
    commit() { engine.commit(); },
  };
}

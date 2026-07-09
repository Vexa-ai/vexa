/** micDictation — REAL-TIME streaming dictation for the chat composer.
 *
 *  Ports the meetings pipeline's delivery mechanism (see
 *  core/meetings/modules/gmeet-pipeline/src/speaker-streams.ts and
 *  core/meetings/modules/buffer — LocalAgreement) to the single-speaker case:
 *
 *   - capture 16 kHz PCM (same ScriptProcessor technique as @vexa/mixed-capture-core);
 *   - a sliding window: every SUBMIT_MS the UNCONFIRMED audio (confirmedSamples → end)
 *     is re-submitted to /api/stt with the confirmed text as the Whisper prompt
 *     (context continuity, exactly like the meeting pipeline);
 *   - LocalAgreement-2: words stable across two consecutive passes CONFIRM; confirmed
 *     audio is trimmed from the window front (confirmedSamples advances to the last
 *     confirmed word's end timestamp); the unstable tail is published as the live
 *     PENDING draft;
 *   - a window cap force-confirms so pending text is never stranded (the pipeline's
 *     TTL idle-finalize equivalent).
 *
 *  Duplicated rather than imported because the terminal image is a self-contained
 *  npm build (no @vexa/* workspace deps — see clients/terminal/Dockerfile).
 *  Transcription stays server-side (/api/stt) so STT credentials never reach the
 *  browser.
 */

export interface SttWord { word: string; start: number; end: number }

export interface StreamingDictation {
  /** Stop capturing, flush the last window, and resolve the FULL final text. */
  stop(): Promise<string>;
  /** Abort immediately: release the mic, discard everything. */
  cancel(): void;
}

export interface StreamingDictationOptions {
  /** Live progress: confirmed text + the unstable pending tail. */
  onUpdate?: (confirmed: string, pending: string) => void;
  /** Non-fatal mid-stream STT failures (the window is retried on the next tick). */
  onError?: (message: string) => void;
}

const SAMPLE_RATE = 16000;
const SUBMIT_MS = 2500;               // meeting pipeline submits ~every 2s; dictation matches
const MIN_WINDOW_SAMPLES = SAMPLE_RATE; // ≥1s of unconfirmed audio before a submission
const FORCE_CONFIRM_SEC = 25;         // window cap: force-confirm so pending never strands

const norm = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

/** Longest common word prefix across two passes (the buffer module's agreement core). */
function agreedPrefixLen(prev: SttWord[], cur: SttWord[]): number {
  let n = 0;
  while (n < prev.length && n < cur.length && norm(prev[n].word) === norm(cur[n].word)) n++;
  return n;
}

export async function startStreamingDictation(opts: StreamingDictationOptions = {}): Promise<StreamingDictation> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
  const source = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);

  const chunks: Float32Array[] = [];
  let totalSamples = 0;
  proc.onaudioprocess = (e: AudioProcessingEvent) => {
    const copy = new Float32Array(e.inputBuffer.getChannelData(0));  // copy — the buffer is reused
    chunks.push(copy);
    totalSamples += copy.length;
  };
  source.connect(proc);
  proc.connect(ctx.destination);  // pull the processor (outputs silence)
  void ctx.resume().catch(() => { /* best-effort */ });

  // ── sliding-window state (mirrors SpeakerBuffer's confirmedSamples/prevWords) ──
  let confirmedSamples = 0;
  let confirmedText = "";
  let prevWords: SttWord[] = [];
  let inFlight = false;
  let stopped = false;

  const windowPcm = (): Float32Array => {
    const out = new Float32Array(totalSamples - confirmedSamples);
    let off = 0, pos = 0;
    for (const c of chunks) {
      const end = pos + c.length;
      if (end > confirmedSamples) {
        const from = Math.max(0, confirmedSamples - pos);
        out.set(c.subarray(from), off);
        off += c.length - from;
      }
      pos = end;
    }
    return out;
  };

  const transcribeWindow = async (pcm: Float32Array): Promise<{ text: string; words: SttWord[] }> => {
    const q = confirmedText ? `?prompt=${encodeURIComponent(confirmedText.slice(-400))}` : "";
    const r = await fetch(`/api/stt${q}`, { method: "POST", headers: { "Content-Type": "audio/wav" }, body: pcmToWav(pcm) });
    const d = (await r.json().catch(() => ({}))) as { text?: string; words?: SttWord[]; error?: string };
    if (!r.ok) throw new Error(d.error || `transcription failed (${r.status})`);
    return { text: (d.text ?? "").trim(), words: d.words ?? [] };
  };

  const joinWords = (ws: SttWord[]) => ws.map((w) => w.word).join("").trim();

  const submit = async () => {
    if (inFlight || stopped) return;
    if (totalSamples - confirmedSamples < MIN_WINDOW_SAMPLES) return;
    inFlight = true;
    try {
      const windowStart = confirmedSamples;                 // detect a concurrent final flush
      const { words } = await transcribeWindow(windowPcm());
      if (stopped || windowStart !== confirmedSamples) return;
      const windowSec = (totalSamples - confirmedSamples) / SAMPLE_RATE;
      let n = agreedPrefixLen(prevWords, words);
      if (n === 0 && windowSec > FORCE_CONFIRM_SEC) n = words.length;  // cap: never strand pending
      if (n > 0) {
        const confirmed = joinWords(words.slice(0, n));
        if (confirmed) confirmedText = confirmedText ? `${confirmedText} ${confirmed}` : confirmed;
        confirmedSamples += Math.min(Math.round(words[n - 1].end * SAMPLE_RATE), totalSamples - confirmedSamples);
        prevWords = [];                                      // window moved — old pass is incomparable
      } else {
        prevWords = words;
      }
      opts.onUpdate?.(confirmedText, joinWords(words.slice(n)));
    } catch (e) {
      opts.onError?.(e instanceof Error ? e.message : "transcription failed");
    } finally { inFlight = false; }
  };

  const timer = setInterval(() => { void submit(); }, SUBMIT_MS);

  const release = () => {
    clearInterval(timer);
    try { proc.disconnect(); proc.onaudioprocess = null; } catch { /* */ }
    stream.getTracks().forEach((t) => t.stop());
    void ctx.close().catch(() => { /* */ });
  };

  const waitIdle = async () => { while (inFlight) await new Promise((r) => setTimeout(r, 60)); };

  return {
    async stop(): Promise<string> {
      stopped = true;
      release();
      await waitIdle();
      // Final flush: whatever audio is still unconfirmed gets one last full-window pass
      // (the pipeline's finalize) — its text is taken verbatim, no agreement needed.
      if (totalSamples - confirmedSamples >= MIN_WINDOW_SAMPLES / 4) {
        try {
          const { text } = await transcribeWindow(windowPcm());
          if (text) confirmedText = confirmedText ? `${confirmedText} ${text}` : text;
        } catch (e) {
          opts.onError?.(e instanceof Error ? e.message : "transcription failed");
        }
      }
      opts.onUpdate?.(confirmedText, "");
      return confirmedText;
    },
    cancel(): void { stopped = true; release(); },
  };
}

/** 16-bit PCM mono WAV (mirrors the whisper module's float32ToWav). */
export function pcmToWav(samples: Float32Array, sampleRate = SAMPLE_RATE): ArrayBuffer {
  const dataSize = samples.length * 2;
  const buf = new ArrayBuffer(44 + dataSize);
  const v = new DataView(buf);
  const ascii = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  ascii(0, "RIFF"); v.setUint32(4, 36 + dataSize, true); ascii(8, "WAVE");
  ascii(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  ascii(36, "data"); v.setUint32(40, dataSize, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

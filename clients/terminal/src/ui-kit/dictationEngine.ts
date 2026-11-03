/**
 * Dictation engine — the composer mic's transcription loop, with no browser in it (the mic wiring is
 * ui-kit/micDictation). Words appear while the person is still speaking:
 *
 *   - every `submitMs` (0.5 s) the audio not yet final is sent as a DRAFT, without waiting for the
 *     previous answer (up to `maxInFlight` at once); the newest answer for the current window wins and
 *     replaces the draft text in place;
 *   - a pause (`pauseMs` of quiet after speech) sends the window once as a FINAL; its answer is
 *     appended to the final text and the window starts again after it;
 *   - a window never grows past `maxWindowSec` — it is finalized there;
 *   - quiet audio is never sent (no invented "Thank you." on silence).
 *
 * Nothing here sends a message: the caller puts `final + draft` in the text box and the person edits
 * and sends it. When the shown text leaves the box (sent, or taken over by the person's edit) the
 * caller calls `commit()`: every answer for audio before that moment is dropped, and only speech
 * after it is shown next.
 */
import { HALLUCINATION_PHRASES } from "./dictationHallucinations";

const PHRASES = new Set(HALLUCINATION_PHRASES.map((p) => p.replace(/[.!?…\s]+$/u, "")));
const key = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}']+/gu, "");

/** Drop sentences Whisper invents on silence ("Продолжение следует…", "Субтитры …", "Thank you.",
 *  "Thanks for watching") and collapse a word looped 3+ times. Same lists as the meeting bot. */
export function cleanDictation(text: string): string {
  const sentences = (text ?? "").match(/[^.!?…]+[.!?…]*/gu) ?? [];
  const kept = sentences.map((raw) => {
    // A phrase invented on the silence after speech is often glued to the end of the real sentence.
    let t = raw.trim();
    for (;;) {
      const low = t.toLowerCase().replace(/[.!?…\s]+$/u, "");
      const tail = [...PHRASES].find((p) => p.length > 3 && low.endsWith(` ${p}`)) ?? low.match(/\s\p{L}+ следует$/u)?.[0]?.trim();
      if (!tail) break;
      t = t.slice(0, low.length - tail.length).trim();
    }
    return t;
  }).filter((raw) => {
    const t = raw.trim().toLowerCase().replace(/[.!?…\s]+$/u, "");
    if (!t) return false;
    if (PHRASES.has(t)) return false;
    if (/^(субтитры|редактор субтитров|subtitles by|transcribed by)\b/u.test(t)) return false;
    if (/^\p{L}+ следует$/u.test(t)) return false;           // "Продолжение следует", "Время следует"
    return true;
  });
  const words = kept.join(" ").trim().split(/\s+/).filter(Boolean);
  const out: string[] = [];
  for (let i = 0; i < words.length;) {
    let j = i + 1;
    while (j < words.length && key(words[j]) !== "" && key(words[j]) === key(words[i])) j++;
    out.push(...(j - i >= 3 ? [words[j - 1]] : words.slice(i, j)));
    i = j;
  }
  return out.join(" ");
}

export interface DictationWord { word: string; start: number; end: number }
export type DictationTranscribe = (pcm: Float32Array, prompt: string, final: boolean) => Promise<{ text: string; words?: DictationWord[] }>;

export interface DictationEngineOptions {
  transcribe: DictationTranscribe;
  onUpdate: (finalText: string, draft: string) => void;
  onError?: (message: string) => void;
  sampleRate?: number;
  submitMs?: number;
  minDraftSec?: number;
  pauseMs?: number;
  maxWindowSec?: number;
  maxInFlight?: number;
  /** RMS at or above which a frame is speech. */
  speechRms?: number;
  now?: () => number;
}

export interface DictationEngine {
  /** One mic frame (mono, `sampleRate`). Returns its RMS (for a level meter). */
  feed(pcm: Float32Array): number;
  /** Called on a timer (every `submitMs`): sends drafts and pause finals. */
  tick(): void;
  /** Stop: finalize what is left and resolve with the final text since the last `commit()`. */
  stop(): Promise<string>;
  /** The text shown so far has left the composer (sent, or absorbed into an edit). Audio up to now
   *  is done with: its late answers (drafts and finals) are dropped; only later speech is shown. */
  commit(): void;
  readonly finalText: string;
}

const rmsOf = (x: Float32Array): number => {
  let s = 0;
  for (let i = 0; i < x.length; i++) s += x[i] * x[i];
  return x.length ? Math.sqrt(s / x.length) : 0;
};

export function createDictationEngine(o: DictationEngineOptions): DictationEngine {
  const SR = o.sampleRate ?? 16000;
  const minDraft = Math.round((o.minDraftSec ?? 0.5) * SR);
  const pauseMs = o.pauseMs ?? 600;
  const maxWindow = Math.round((o.maxWindowSec ?? 10) * SR);
  const maxInFlight = o.maxInFlight ?? 3;
  const speechRms = o.speechRms ?? 0.01;
  /** A window is sent only with this much audio clearly above the noise floor (4× it). */
  const minSpeech = Math.round(0.25 * SR);
  /** Frame levels of the last ~3 s; the noise floor is their 10th percentile (steady noise becomes
   *  the floor within a second, while the dips between words keep speech above it). */
  const recent: number[] = [];
  let recentSamples = 0;
  const noiseFloor = () => {
    const v = [...recent].sort((a, b) => a - b);
    const p10 = v[Math.floor(0.1 * (v.length - 1))] ?? 0;
    // Under a second of history the floor is unknown: assume a quiet room (the browser's own noise
    // suppression already removes steady fans) so speech from the first frame is not held back.
    const known = recentSamples >= SR;
    return Math.max(0.0005, known ? p10 : Math.min(p10, 0.003));
  };
  const submitMs = o.submitMs ?? 500;
  let lastDraftAt = -Infinity;
  const now = o.now ?? (() => Date.now());

  const chunks: { pcm: Float32Array; voiced: boolean }[] = [];
  let total = 0;                 // samples fed
  let windowStart = 0;           // first sample not yet final
  let lastVoicedEnd = -1;        // sample index just past the last voiced frame
  let quietSince: number | null = null;
  let voicedSinceFinal = false;
  let finalText = "";
  let draft = "";
  let seq = 0, acceptedSeq = 0, inFlight = 0;
  let sentUpTo = 0;              // window end of the newest request
  let finalOwed: Promise<void> | null = null;
  let committedAt = 0;           // sample index of the last commit(); audio before it is done with
  let context = "";              // committed text, kept only as the STT prompt's left context
  let stopped = false;

  const slice = (from: number, to: number): Float32Array => {
    const out = new Float32Array(Math.max(0, to - from));
    let pos = 0, off = 0;
    for (const c of chunks) {
      const end = pos + c.pcm.length;
      if (end > from && pos < to) {
        const a = Math.max(from, pos) - pos, b = Math.min(to, end) - pos;
        out.set(c.pcm.subarray(a, b), off);
        off += b - a;
      }
      pos = end;
      if (pos >= to) break;
    }
    return out;
  };
  const windowVoiced = (from: number, to: number): boolean => {
    let pos = 0, voiced = 0;
    for (const c of chunks) {
      const end = pos + c.pcm.length;
      if (end > from && pos < to && c.voiced) voiced += Math.min(end, to) - Math.max(pos, from);
      pos = end;
      if (pos >= to) break;
    }
    return voiced >= Math.min(minSpeech, Math.max(1, to - from));
  };
  const publish = () => o.onUpdate(finalText, draft);
  const join = (a: string, b: string) => (a && b ? `${a} ${b}` : a || b);
  const prompt = () => join(context, finalText).slice(-400);

  const sendDraft = () => {
    const from = windowStart, to = total;
    if (to - from < minDraft || !windowVoiced(from, to) || inFlight >= maxInFlight || to <= sentUpTo) return;
    if (now() - lastDraftAt < submitMs) return;
    lastDraftAt = now();
    const mine = ++seq;
    inFlight++;
    sentUpTo = to;
    o.transcribe(slice(from, to), prompt(), false).then((r) => {
      // Only the newest answer for the CURRENT window is shown; a window that moved is stale.
      if (stopped) return;
      if (from !== windowStart || mine < acceptedSeq) return;
      acceptedSeq = mine;
      draft = cleanDictation(r.text ?? "");
      publish();
    }).catch((e) => o.onError?.(e instanceof Error ? e.message : "transcription failed"))
      .finally(() => { inFlight--; });
  };

  const sendFinal = (to: number): Promise<void> => {
    const from = windowStart;
    windowStart = to;              // later drafts start after this window
    sentUpTo = to;
    acceptedSeq = ++seq;           // every draft still in flight is now stale
    voicedSinceFinal = false;
    if (!windowVoiced(from, to)) { draft = ""; publish(); return Promise.resolve(); }
    // Sent at once (finals run in parallel), appended in order: each waits for the one before it.
    const answer = (async (): Promise<string> => {
      for (let attempt = 0; attempt < 3; attempt++) {
        try { return cleanDictation((await o.transcribe(slice(from, to), prompt(), true)).text ?? ""); }
        catch (e) {
          o.onError?.(e instanceof Error ? e.message : "transcription failed");
          await new Promise((res) => setTimeout(res, 300 * (attempt + 1)));
        }
      }
      return "";
    })();
    const prev = finalOwed ?? Promise.resolve();
    const mine: Promise<void> = prev.then(async () => {
      const text = await answer;
      if (to <= committedAt) return;   // this window's text already left the composer
      finalText = join(finalText, text);
      if (windowStart === to) draft = "";
      publish();
    });
    finalOwed = mine;
    void mine.finally(() => { if (finalOwed === mine) finalOwed = null; });
    return mine;
  };

  return {
    feed(pcm) {
      const level = rmsOf(pcm);
      recent.push(level);
      recentSamples += pcm.length;
      if (recent.length > Math.ceil((3 * SR) / Math.max(1, pcm.length))) recent.shift();
      const voiced = level >= Math.max(speechRms, Math.min(4 * noiseFloor(), 0.03));   // loud is always speech
      chunks.push({ pcm, voiced });
      total += pcm.length;
      if (voiced) { lastVoicedEnd = total; quietSince = null; voicedSinceFinal = true; }
      else if (quietSince === null) quietSince = now();
      return level;
    },
    tick() {
      if (stopped) return;
      if (voicedSinceFinal && quietSince !== null && now() - quietSince >= pauseMs && lastVoicedEnd > windowStart) {
        void sendFinal(total);
        return;
      }
      if (total - windowStart >= maxWindow) { void sendFinal(total); return; }
      sendDraft();
    },
    async stop() {
      stopped = true;
      if (total > windowStart) await sendFinal(total);
      while (finalOwed) await finalOwed;
      draft = "";
      publish();
      return finalText;
    },
    commit() {
      // Windows are sample-aligned and the next one starts here, so no window straddles the cut:
      // every window in flight ends at or before it and is dropped whole when it answers.
      committedAt = total;
      windowStart = total;
      sentUpTo = total;
      acceptedSeq = ++seq;         // drafts in flight belong to committed audio
      voicedSinceFinal = false;
      quietSince = null;
      context = join(context, finalText).slice(-400);
      finalText = "";
      draft = "";
      publish();
    },
    get finalText() { return finalText; },
  };
}

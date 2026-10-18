// Dictation bench (bbb): a person speaks 8 sentences into the composer mic (synthetic TTS, real time);
// measure speech start → first word of that sentence in the box, and speech end → its final text.
// Usage: STT_URL=… STT_TOKEN=… npx tsx dictbench.ts <new|old> <fixtureDir>
import { readFileSync } from "node:fs";
import { createDictationEngine } from "../../src/ui-kit/dictationEngine";
import { encodeFlac } from "../../src/ui-kit/flac";
import { pcmToWavLegacy } from "./legacywav";
const [mode, dir] = process.argv.slice(2);
const SR = 16000, FRAME = 1024;
const fx = JSON.parse(readFileSync(`${dir}/fixture.json`, "utf8"));
const utt = fx.utterances.slice(0, 8).map((u: any) => { const b = readFileSync(`${dir}/${u.file}`); return { ...u, pcm: new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)) }; });
// Re-time as one person: sentences back to back with 1.0 s pauses.
let t = 500; for (const u of utt) { u.s = t; u.e = t + (u.pcm.length / SR) * 1000; t = u.e + 1000; }
const total = Math.ceil(((t + 2000) / 1000) * SR); const mic = new Float32Array(total);
for (const u of utt) mic.set(u.pcm, Math.floor((u.s / 1000) * SR));
const norm = (x: string) => x.toLowerCase().replace(/[^\p{L}\p{N}' ]/gu, " ").split(/\s+/).filter(Boolean);
let bytes = 0, requests = 0;
async function stt(pcm: Float32Array, prompt: string, flac: boolean) {
  const form = new FormData();
  const body = flac ? encodeFlac(pcm, SR) : new Uint8Array(pcmToWavLegacy(pcm));
  bytes += body.length; requests++;
  form.append("file", new Blob([body]), flac ? "d.flac" : "d.wav");
  form.append("model", "whisper-1"); form.append("response_format", "verbose_json"); form.append("timestamp_granularities", "word");
  if (prompt) form.append("prompt", prompt.slice(-400));
  const url = process.env.STT_URL!.replace(/\/+$/, ""); const ep = url.endsWith("/v1/audio/transcriptions") ? url : url + "/v1/audio/transcriptions";
  const r = await fetch(ep, { method: "POST", headers: { Authorization: `Bearer ${process.env.STT_TOKEN}` }, body: form });
  const d: any = await r.json();
  const words = (d.segments ?? []).flatMap((s: any) => s.words ?? []).map((w: any) => ({ word: w.word, start: w.start ?? 0, end: w.end ?? 0 }));
  return { text: (d.text ?? "").trim(), words };
}
let shown = ""; let finalShown = "";
const firstSeen: (number | undefined)[] = utt.map(() => undefined), finalSeen: (number | undefined)[] = utt.map(() => undefined);
const T0 = Date.now() + 200;
const check = () => {
  const vis = new Set(norm(shown)), fin = new Set(norm(finalShown)); const now = Date.now() - T0;
  utt.forEach((u: any, i: number) => {
    const w = norm(u.text).filter((x) => x.length > 3);
    if (firstSeen[i] === undefined && now >= u.s && w.some((x) => vis.has(x))) firstSeen[i] = now;
    if (finalSeen[i] === undefined && w.filter((x) => fin.has(x)).length / w.length >= 0.6) finalSeen[i] = now;
  });
};
type Eng = { feed(p: Float32Array): void; tick(): void; stop(): Promise<string> };
let eng: Eng;
if (mode === "new") {
  eng = createDictationEngine({ transcribe: (p, pr) => stt(p, pr, true), onUpdate: (f, d) => { finalShown = f; shown = f + " " + d; check(); } });
} else {
  // The composer's previous loop: every 2.5 s, ≥1 s of unconfirmed audio, one request at a time,
  // two agreeing passes confirm, WAV upload.
  const chunks: Float32Array[] = []; let total = 0, confirmed = 0, confText = "", prev: any[] = [], busy = false;
  const win = () => { const out = new Float32Array(total - confirmed); let off = 0, pos = 0; for (const c of chunks) { const end = pos + c.length; if (end > confirmed) { const f = Math.max(0, confirmed - pos); out.set(c.subarray(f), off); off += c.length - f; } pos = end; } return out; };
  const nn = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
  let last = 0;
  eng = {
    feed(p) { chunks.push(p); total += p.length; },
    tick() {
      if (Date.now() - last < 2500 || busy || total - confirmed < SR) return; last = Date.now(); busy = true;
      const start = confirmed;
      stt(win(), confText, false).then(({ words }) => {
        if (start !== confirmed) return;
        let n = 0; while (n < prev.length && n < words.length && nn(prev[n].word) === nn(words[n].word)) n++;
        if (n > 0) { const c = words.slice(0, n).map((w: any) => w.word).join("").trim(); confText = confText ? confText + " " + c : c; confirmed += Math.min(Math.round(words[n - 1].end * SR), total - confirmed); prev = []; }
        else prev = words;
        finalShown = confText; shown = confText + " " + words.slice(n).map((w: any) => w.word).join(""); check();
      }).finally(() => { busy = false; });
    },
    async stop() { while (busy) await new Promise((r) => setTimeout(r, 50)); const { text } = await stt(win(), confText, false); confText = confText ? confText + " " + text : text; finalShown = shown = confText; check(); return confText; },
  };
}
for (let o = 0; o < total; o += FRAME) {
  const wait = T0 + (o / SR) * 1000 - Date.now(); if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  eng.feed(mic.slice(o, Math.min(total, o + FRAME))); eng.tick(); check();
}
await eng.stop(); check();
const lat = (arr: (number | undefined)[], ref: (u: any) => number) => utt.map((u: any, i: number) => arr[i] === undefined ? Infinity : (arr[i]! - ref(u)) / 1000).sort((a: number, b: number) => a - b);
const q = (v: number[], p: number) => { const x = v[Math.min(v.length - 1, Math.ceil(p * v.length) - 1)]; return Number.isFinite(x) ? +x.toFixed(1) : null; };
const first = lat(firstSeen, (u) => u.s), fin = lat(finalSeen, (u) => u.e);
console.log(JSON.stringify({ mode, first_words_after_speech_start_s: { p50: q(first, 0.5), p95: q(first, 0.95) }, final_after_speech_end_s: { p50: q(fin, 0.5), p95: q(fin, 0.95) }, requests, kb: Math.round(bytes / 1024) }));
process.exit(0);

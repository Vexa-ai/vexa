/**
 * Transcription guarantee harness (#1783) — replays a known multi-speaker recording through the
 * bot's REAL lane for one platform (createBotPipeline: Meet per-channel + glow names, Zoom per-track,
 * Teams mixed audio + CSRC transport + speaker hints, voice note single owner channel) against a REAL
 * STT, in real time, with an injected fault, and prints one JSON line:
 *
 *   latency    utterance end → first visible text holding ≥80% of its words (p50/p95/p99, s)
 *   efficiency request bytes per audio second, largest request (bytes, audio s), peak requests in flight
 *   completeness ground-truth word recall in the final transcript, and the lane's word loss
 *   attribution share of matched words published under the right speaker
 *   lean       this process's CPU seconds and peak RSS (the lane only — no browser)
 *
 *   STT_URL=… STT_TOKEN=… npx tsx eval/guarantee.ts --fixture <dir> --platform google_meet --fault none
 *
 * Faults: none · hang (the 3rd request never answers for 120 s) · slow3x (every answer takes 3× as
 * long) · outage60 (every request fails from t=20 s to t=80 s) · loss5 (5% of requests fail).
 * Fixture: <dir>/fixture.json {sampleRate, durationMs, utterances:[{speaker,text,startMs,endMs,file}]}
 * with each utterance's mono f32le PCM in <dir>/<file>. Generated from synthetic voices, never from a
 * real meeting.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createBotPipeline } from '../src/pipeline.js';
import { TranscriptionClient } from '@vexa/transcribe-whisper';

const arg = (k: string, d?: string) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const dir = arg('fixture')!;
const platform = arg('platform', 'google_meet')!;
const fault = arg('fault', 'none')!;
const FRAME = 2048;
const fx = JSON.parse(readFileSync(join(dir, 'fixture.json'), 'utf8')) as {
  sampleRate: number; durationMs: number; utterances: { speaker: string; text: string; startMs: number; endMs: number; file: string }[];
};
const SR = fx.sampleRate;
const pcmOf = (f: string) => { const b = readFileSync(join(dir, f)); return new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)); };
const utt = fx.utterances.map((u) => ({ ...u, pcm: pcmOf(u.file) }));
const speakers = [...new Set(utt.map((u) => u.speaker))];
const OWNER = 'Owner';
const expectSpeaker = (s: string) => (platform === 'voice_note' ? OWNER : s);

// ── the wire: count bytes, inject faults ───────────────────────────────────────────────────────────
const realFetch = globalThis.fetch;
const T0 = Date.now() + 500;
let sentAudioSec = 0, sttSec = 0;
let reqNo = 0, inFlight = 0, peakInFlight = 0, bytes = 0, maxBytes = 0, maxAudioSec = 0, failedReq = 0;
const sleep = (ms: number, signal?: AbortSignal | null) => new Promise<void>((res, rej) => {
  const t = setTimeout(res, ms);
  signal?.addEventListener('abort', () => { clearTimeout(t); rej(Object.assign(new Error('aborted'), { name: 'AbortError' })); });
});
/** Audio seconds in an upload body (WAV data size, or FLAC STREAMINFO total samples). */
function bodyAudioSec(body: Buffer): number {
  const i = body.indexOf('RIFF');
  if (i >= 0) return (body.readUInt32LE(i + 40) / 2) / SR;
  const f = body.indexOf('fLaC');
  if (f >= 0) { const s = f + 8; const hi = body[s + 13] & 0x0f; return (hi * 2 ** 32 + body.readUInt32BE(s + 14)) / SR; }
  return 0;
}
globalThis.fetch = (async (url: string, init: RequestInit & { body?: Buffer }) => {
  const n = ++reqNo;
  const body = init.body as Buffer;
  const bodySec = bodyAudioSec(body);
  bytes += body.length; sentAudioSec += bodySec; maxBytes = Math.max(maxBytes, body.length); maxAudioSec = Math.max(maxAudioSec, bodySec);
  inFlight++; peakInFlight = Math.max(peakInFlight, inFlight);
  const sinceStart = Date.now() - T0;
  try {
    if (fault === 'hang' && n === 3) await sleep(120_000, init.signal);
    if (fault === 'outage60' && sinceStart > 20_000 && sinceStart < 80_000) { failedReq++; throw new TypeError('fetch failed (injected outage)'); }
    if (fault === 'loss5' && Math.random() < 0.05) { failedReq++; throw new TypeError('fetch failed (injected loss)'); }
    const t = Date.now();
    const r = await realFetch(url, init);
    sttSec += (Date.now() - t) / 1000;
    if (fault === 'slow3x') { const text = await r.text(); await sleep(2 * (Date.now() - t), init.signal); return new Response(text, { status: r.status, headers: r.headers }); }
    return r;
  } finally { inFlight--; }
}) as typeof fetch;

const client = new TranscriptionClient({ serviceUrl: process.env.STT_URL!, apiToken: process.env.STT_TOKEN });
const language = arg('language', 'en')!;
const transcribe = (pcm: Float32Array, prompt?: string) => client.transcribe(pcm, language === 'auto' ? undefined : language, prompt);

// ── the sink: what a viewer sees ───────────────────────────────────────────────────────────────────
type Seg = { segment_id: string; speaker?: string; text: string; start?: number; end?: number; completed?: boolean; absolute_start_time?: string; absolute_end_time?: string };
const visible = new Map<string, Seg & { at: number }>();
const sink = {
  async publish(s: Seg) { const prev = visible.get(s.segment_id); visible.set(s.segment_id, { ...s, at: prev && prev.text === s.text ? prev.at : Date.now() }); sample(); },
  async retract(ids: string[]) { for (const id of ids) visible.delete(id); },
};
const norm = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{N}' ]/gu, ' ').split(/\s+/).filter(Boolean);
const segMs = (s: Seg) => ({
  a: s.absolute_start_time ? Date.parse(s.absolute_start_time) : (s.start ?? 0) * 1000,
  b: s.absolute_end_time ? Date.parse(s.absolute_end_time) : (s.end ?? s.start ?? 0) * 1000,
});
// Latency: the first moment an utterance's words are ≥80% visible (any segment overlapping it in time).
// A: confirmed segments only; B: anything visible (drafts included).
const seenAt: (number | undefined)[] = utt.map(() => undefined);
const draftAt: (number | undefined)[] = utt.map(() => undefined);
function sample() {
  const now = Date.now();
  utt.forEach((u, i) => {
    if (seenAt[i] !== undefined) return;
    const a = T0 + u.startMs - 1500, b = T0 + u.endMs + 1500;
    const any = new Set<string>(), conf = new Set<string>();
    for (const s of visible.values()) {
      const m = segMs(s);
      if (m.b < a || m.a > b) continue;
      for (const w of norm(s.text)) { any.add(w); if (s.completed !== false) conf.add(w); }
    }
    const want = norm(u.text);
    if (draftAt[i] === undefined && want.filter((w) => any.has(w)).length / want.length >= 0.8) draftAt[i] = now;
    if (want.filter((w) => conf.has(w)).length / want.length >= 0.8) seenAt[i] = now;
  });
}
// C: the customer's unit — every 20 s, how old is the newest confirmed segment (read time − its end).
const pollLag: number[] = [];
const pollTimer = setInterval(() => {
  let newest = 0;
  for (const s of visible.values()) if (s.completed !== false && s.text.trim()) newest = Math.max(newest, segMs(s).b);
  if (newest) pollLag.push((Date.now() - newest) / 1000);
}, 20_000);

// ── drive the lane in real time ────────────────────────────────────────────────────────────────────
const inv = { platform, language: language === 'auto' ? undefined : language, botName: 'Vexa', transcribeEnabled: true } as never;
const variant = arg('variant', 'baseline')!;
const laneConfig = JSON.parse(arg('config', '{}')!);
const lane = createBotPipeline(inv, sink as never, { transcribe, onError: () => {}, config: Object.keys(laneConfig).length ? laneConfig : undefined });
type Ev = { t: number; run: () => void };
const evs: Ev[] = [];
const chan = (s: string) => speakers.indexOf(s);
if (platform === 'teams' || platform === 'voice_note') {
  // One stream: the whole room mixed (Teams) or the owner's microphone (voice note), continuous.
  const total = Math.ceil((fx.durationMs / 1000) * SR);
  const mix = new Float32Array(total);
  for (const u of utt) mix.set(u.pcm, Math.floor((u.startMs / 1000) * SR));
  for (let o = 0; o < total; o += FRAME) {
    const t = (o / SR) * 1000, frame = mix.subarray(o, Math.min(total, o + FRAME));
    evs.push({ t, run: () => platform === 'teams' ? lane.feedMixedAudio(frame, T0 + t) : lane.feedAudio(0, OWNER, frame, T0 + t) });
  }
  if (platform === 'teams') for (const u of utt) {
    const csrc = 1000 + chan(u.speaker);
    evs.push({ t: u.startMs - 1, run: () => { lane.recordTransportEvent?.({ csrc, active: true, tMs: T0 + u.startMs } as never); lane.recordHint(u.speaker, T0 + u.startMs, false); } });
    evs.push({ t: u.endMs + 1, run: () => { lane.recordTransportEvent?.({ csrc, active: false, tMs: T0 + u.endMs } as never); lane.recordHint(u.speaker, T0 + u.endMs, true); } });
  }
} else {
  // Per-participant tracks (Meet channels, Zoom tracks): a speaker's frames arrive while they speak.
  for (const u of utt) for (let o = 0; o < u.pcm.length; o += FRAME) {
    const t = u.startMs + (o / SR) * 1000, frame = u.pcm.subarray(o, Math.min(u.pcm.length, o + FRAME));
    evs.push({ t, run: () => lane.feedAudio(chan(u.speaker), u.speaker, frame, T0 + t) });
  }
}
evs.sort((x, y) => x.t - y.t);

let peakRss = 0;
const rssTimer = setInterval(() => { peakRss = Math.max(peakRss, process.memoryUsage().rss); sample(); }, 250);
await lane.start();
for (const e of evs) { const wait = T0 + e.t - Date.now(); if (wait > 0) await sleep(wait); e.run(); }
const stopAt = Date.now();
await lane.stop();
const stopMs = Date.now() - stopAt;
clearInterval(rssTimer); sample();

// ── score: in-order alignment of reference words against the published words ──────────────────
clearInterval(pollTimer);
const finals = [...visible.values()].filter((s) => s.completed !== false && s.text.trim())
  .sort((x, y) => segMs(x).a - segMs(y).a);
const ref: { w: string; spk: string; i: number; t: number }[] = [];
utt.forEach((u, i) => { for (const w of norm(u.text)) ref.push({ w, spk: expectSpeaker(u.speaker), i, t: u.startMs }); });
const hyp: { w: string; spk: string }[] = [];
for (const s of finals) for (const w of norm(s.text)) hyp.push({ w, spk: s.speaker ?? '' });
// LCS over words (rolling rows kept for the backtrack).
const R = ref.length, H = hyp.length;
const dp = new Uint16Array((R + 1) * (H + 1));
for (let i = 1; i <= R; i++) for (let j = 1; j <= H; j++) {
  const k = i * (H + 1) + j;
  dp[k] = ref[i - 1].w === hyp[j - 1].w ? dp[k - H - 2] + 1 : Math.max(dp[k - H - 1], dp[k - 1]);
}
const hit = new Array<string | null>(R).fill(null);
for (let i = R, j = H; i > 0 && j > 0;) {
  const k = i * (H + 1) + j;
  if (ref[i - 1].w === hyp[j - 1].w && dp[k] === dp[k - H - 2] + 1) { hit[i - 1] = hyp[j - 1].spk; i--; j--; }
  else if (dp[k - H - 1] >= dp[k - 1]) i--; else j--;
}
const matchedN = hit.filter((h) => h !== null).length;
const rightN = hit.filter((h, i) => h !== null && h === ref[i].spk).length;
const bucket = (key: (r: typeof ref[number]) => string) => {
  const m = new Map<string, [number, number]>();
  ref.forEach((r, i) => { const k = key(r); const v = m.get(k) ?? [0, 0]; v[1]++; if (hit[i] !== null) v[0]++; m.set(k, v); });
  return Object.fromEntries([...m].map(([k, [h, n]]) => [k, +(h / n).toFixed(3)]));
};
const pct = (arr: number[], p: number) => { const v = [...arr].sort((x, y) => x - y); return v.length ? +v[Math.min(v.length - 1, Math.ceil(p * v.length) - 1)].toFixed(1) : null; };
const latOf = (at: (number | undefined)[]) => utt.map((u, i) => (at[i] !== undefined ? (at[i]! - (T0 + u.endMs)) / 1000 : Infinity));
const latA = latOf(seenAt), latB = latOf(draftAt);
const finite = (v: number[]) => v.filter(Number.isFinite);
const audioSec = utt.reduce((n, u) => n + u.pcm.length / SR, 0);
const cpu = process.cpuUsage();
console.log(JSON.stringify({
  platform, fault, variant, config: laneConfig,
  latency_A_confirmed_s: { p50: pct(finite(latA), 0.5), p95: pct(finite(latA), 0.95), p99: pct(finite(latA), 0.99), never: latA.filter((v) => !Number.isFinite(v)).length },
  latency_B_any_visible_s: { p50: pct(finite(latB), 0.5), p95: pct(finite(latB), 0.95), never: latB.filter((v) => !Number.isFinite(v)).length },
  latency_C_newest_confirmed_lag_at_20s_polls_s: { median: pct(pollLag, 0.5), p90: pct(pollLag, 0.9), max: pct(pollLag, 1), reads: pollLag.length },
  efficiency: { requests: reqNo, failed_injected: failedReq, bytes_per_audio_s: Math.round(bytes / audioSec), max_request_bytes: maxBytes,
    max_request_audio_s: +maxAudioSec.toFixed(1), peak_in_flight: peakInFlight,
    requests_per_min: +(reqNo / (fx.durationMs / 60_000)).toFixed(1),
    reprocess_factor: +(sentAudioSec / audioSec).toFixed(2), stt_rtf: sentAudioSec ? +(sttSec / sentAudioSec).toFixed(2) : null },
  completeness: { in_order_coverage: +(matchedN / R).toFixed(3), ref_words: R, per_10min: bucket((r) => String(Math.floor(r.t / 600_000))),
    per_speaker_count: Object.keys(bucket((r) => r.spk)).length, per_speaker: Object.values(bucket((r) => r.spk)), stop_to_complete_s: +(stopMs / 1000).toFixed(1) },
  extra_words: { published: H, inserted: H - matchedN, ...(process.env.SHOW_TEXT === 'synthetic' ? { text: finals.map((s) => s.text).join(' | ') } : {}) },
  attribution: { matched_word_accuracy: matchedN ? +(rightN / matchedN).toFixed(3) : null },
  lean: { cpu_s: +((cpu.user + cpu.system) / 1e6).toFixed(1), peak_rss_mb: Math.round(peakRss / 2 ** 20) },
}));
process.exit(0);

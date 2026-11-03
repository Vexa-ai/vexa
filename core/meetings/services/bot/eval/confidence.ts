/**
 * STT confidence-filter harness (#1783). Sends lane-sized windows to a REAL STT (verbose_json) and
 * records, per returned segment, the confidence fields Whisper reports (avg_logprob, no_speech_prob,
 * compression_ratio), the segment's audio level, and whether the filters that already run would keep
 * it (the client's low-confidence rule, its silent-segment rule, the lane's phrase filter). A
 * separate `--report` pass scores candidate confidence rules against those rows.
 *
 *   STT_URL=… STT_TOKEN=… npx tsx eval/confidence.ts --set <name> --out rows.jsonl
 *        ( --tapes <dir> [--match <substr>] [--max-windows 40] | --fixture <dir> | --nonspeech )
 *        [--text-out private.jsonl]    # segment text, for a private review only — never commit it
 *   npx tsx eval/confidence.ts --report rows.jsonl [more.jsonl …]
 *
 * Sources:
 *   --tapes      capture.v1 tapes (desktop/extension recordings); each track is cut into 10 s windows,
 *                and only windows that pass the lane's speech gate are sent — the filter under test
 *                runs IN ADDITION to that gate.
 *   --fixture    synthetic fixture {sampleRate, utterances:[{text,startMs,endMs,file}]}; every window
 *                carries its ground-truth words, so word recall is exact.
 *   --nonspeech  generated silence, noise, hum, clicks and music: every word returned is invented.
 * Rows never carry segment text (only a word count and the phrase-filter verdict); --text-out writes
 * text to a separate file for review. Audio never leaves the machine except to the STT.
 */
import { readFileSync, writeFileSync, appendFileSync, readdirSync } from 'node:fs';
import { join, basename } from 'node:path';
import { createHash } from 'node:crypto';
import { isLowConfidenceSegment } from '@vexa/transcribe-whisper';
import { isHallucination } from '@vexa/gmeet-pipeline';

/** Candidate rules scored by --report (a segment is DROPPED when the rule returns true). */
const nsp = (s: any) => s.no_speech_prob ?? 0, lp = (s: any) => s.avg_logprob ?? 0, cr = (s: any) => s.compression_ratio ?? 0;
const CANDIDATES: Record<string, (s: any) => boolean> = {
  'nsp>0.5&lp<-0.5': (s) => nsp(s) > 0.5 && lp(s) < -0.5,
  'nsp>0.4&lp<-0.7': (s) => nsp(s) > 0.4 && lp(s) < -0.7,
  'nsp>0.6': (s) => nsp(s) > 0.6,
  'nsp>0.8': (s) => nsp(s) > 0.8,
  'lp<-0.8': (s) => lp(s) < -0.8,
  'lp<-1.0': (s) => lp(s) < -1.0,
  'cr>2.0': (s) => cr(s) > 2.0,
  'cr>1.8': (s) => cr(s) > 1.8,
  'combo-a': (s) => (nsp(s) > 0.5 && lp(s) < -0.5) || lp(s) < -1.0 || cr(s) > 2.0,
  'combo-b': (s) => (nsp(s) > 0.6 && lp(s) < -0.4) || cr(s) > 2.2,
  'strict (shipped)': (s) => isLowConfidenceSegment(s, 'strict'),
};

const SR = 16000;
const WIN = 10 * SR;
const arg = (k: string, d?: string) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const has = (k: string) => process.argv.includes(`--${k}`);

const rmsOf = (a: Float32Array, from = 0, to = a.length) => {
  let s = 0; const n = Math.max(1, to - from);
  for (let i = from; i < to; i++) s += a[i] * a[i];
  return Math.sqrt(s / n);
};

/** The lane's speech gate (gmeet-pipeline speaker-streams): window RMS ≥ 0.0025 and ≥ 0.25 s of 512-
 *  sample frames above max(0.0025, min(4 × noise floor, 0.02)); the floor follows 1024-sample frames. */
function gateWindows(stream: Float32Array): { from: number; to: number; gate: boolean }[] {
  const out: { from: number; to: number; gate: boolean }[] = [];
  let floor: number | undefined;
  const floorAt: number[] = [];
  for (let i = 0; i < stream.length; i += 1024) {
    const level = rmsOf(stream, i, Math.min(stream.length, i + 1024));
    floor = floor === undefined ? Math.min(level, 0.003) : level < floor ? level : floor + 0.002 * (level - floor);
    floorAt.push(floor);
  }
  for (let from = 0; from + SR < stream.length; from += WIN) {
    const to = Math.min(stream.length, from + WIN);
    const f = floorAt[Math.min(floorAt.length - 1, Math.floor(to / 1024))] ?? 0;
    const bar = Math.max(0.0025, Math.min(4 * f, 0.02));
    let voiced = 0;
    for (let i = from; i + 512 <= to; i += 512) if (rmsOf(stream, i, i + 512) >= bar) voiced += 512;
    out.push({ from, to, gate: rmsOf(stream, from, to) >= 0.0025 && voiced / SR >= 0.25 });
  }
  return out;
}

function wav(pcm: Float32Array): Buffer {
  const b = Buffer.alloc(44 + pcm.length * 2);
  b.write('RIFF', 0); b.writeUInt32LE(36 + pcm.length * 2, 4); b.write('WAVE', 8); b.write('fmt ', 12);
  b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22); b.writeUInt32LE(SR, 24);
  b.writeUInt32LE(SR * 2, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write('data', 36);
  b.writeUInt32LE(pcm.length * 2, 40);
  for (let i = 0; i < pcm.length; i++) b.writeInt16LE(Math.round(Math.max(-1, Math.min(1, pcm[i])) * 32767), 44 + i * 2);
  return b;
}

async function stt(pcm: Float32Array): Promise<any> {
  const url = process.env.STT_URL!.replace(/\/+$/, '') + (/\/v1\/audio\/transcriptions$/.test(process.env.STT_URL!) ? '' : '/v1/audio/transcriptions');
  for (let attempt = 0; attempt < 6; attempt++) {
    const fd = new FormData();
    fd.append('file', new Blob([wav(pcm)], { type: 'audio/wav' }), 'audio.wav');
    fd.append('model', 'whisper-1');
    fd.append('response_format', 'verbose_json');
    fd.append('timestamp_granularities', 'word');
    const r = await fetch(url, { method: 'POST', body: fd, headers: process.env.STT_TOKEN ? { Authorization: `Bearer ${process.env.STT_TOKEN}` } : {} });
    if (r.ok) return r.json();
    await new Promise((res) => setTimeout(res, 1000 * (attempt + 1)));   // shared GPU: back off, never storm
  }
  throw new Error('STT kept refusing');
}

const norm = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}']+/gu, '');
const words = (t: string) => (t ?? '').split(/\s+/).map(norm).filter(Boolean);

type Win = { src: string; track: string; pcm: Float32Array; gate: boolean; ref?: string[]; offsetSec: number };

function* tapeWindows(dir: string, match: string | undefined, maxPerTape: number): Generator<Win> {
  for (const f of readdirSync(dir).filter((x) => x.endsWith('.jsonl') && (!match || x.includes(match))).sort()) {
    const tracks = new Map<number, Float32Array[]>();
    for (const line of readFileSync(join(dir, f), 'utf8').split('\n')) {
      if (!line.includes('"bin":true')) continue;
      const b = Buffer.from(JSON.parse(line).d, 'base64');
      if (b.length < 12) continue;
      const raw = b.readInt32LE(0);
      let start = 12, idx = raw;
      if (raw & 0x80000000) { idx = raw & 0x7fffffff; const n = b.readInt32LE(12); start = 16 + ((n + 3) & ~3); }
      const n = (b.length - start) >> 2;
      const s = new Float32Array(n);
      for (let i = 0; i < n; i++) s[i] = b.readFloatLE(start + i * 4);
      (tracks.get(idx) ?? tracks.set(idx, []).get(idx)!).push(s);
    }
    const src = createHash('sha256').update(f).digest('hex').slice(0, 10);
    for (const [idx, parts] of tracks) {
      const len = parts.reduce((a, p) => a + p.length, 0);
      const stream = new Float32Array(len); let o = 0;
      for (const p of parts) { stream.set(p, o); o += p.length; }
      const ws = gateWindows(stream).filter((w) => w.gate);
      // spread the sample across the track rather than taking its start
      const step = Math.max(1, Math.ceil(ws.length / maxPerTape));
      for (let k = 0; k < ws.length; k += step) {
        const w = ws[k];
        yield { src: `${f.split('-')[1]}:${src}`, track: String(idx), pcm: stream.subarray(w.from, w.to), gate: true, offsetSec: w.from / SR };
      }
    }
  }
}

function* fixtureWindows(dir: string): Generator<Win> {
  const fx = JSON.parse(readFileSync(join(dir, 'fixture.json'), 'utf8'));
  const total = Math.ceil(fx.durationMs / 1000 * SR) + SR;
  const stream = new Float32Array(total);
  for (const u of fx.utterances) {
    const b = readFileSync(join(dir, u.file));
    const pcm = new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
    stream.set(pcm.subarray(0, Math.max(0, total - Math.round(u.startMs / 1000 * SR))), Math.round(u.startMs / 1000 * SR));
  }
  for (const w of gateWindows(stream)) {
    // reference: utterances whose midpoint falls inside the window (windows cut mid-word lose a word
    // at the edge either way — the same for filter on and off)
    const ref = fx.utterances.filter((u: any) => { const m = (u.startMs + u.endMs) / 2 / 1000 * SR; return m >= w.from && m < w.to; })
      .flatMap((u: any) => words(u.text));
    yield { src: basename(dir), track: '0', pcm: stream.subarray(w.from, w.to), gate: w.gate, ref, offsetSec: w.from / SR };
  }
}

function* nonspeechWindows(): Generator<Win> {
  let seed = 7;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const kinds: [string, (i: number, n: number) => number][] = [];
  for (const lvl of [0.0005, 0.005, 0.02, 0.06]) kinds.push([`white-${lvl}`, () => (rnd() * 2 - 1) * lvl * 1.73]);
  let pink = 0;
  for (const lvl of [0.01, 0.04]) kinds.push([`brown-${lvl}`, () => { pink = 0.98 * pink + (rnd() * 2 - 1) * 0.2; return pink * lvl * 3; }]);
  for (const lvl of [0.01, 0.05]) kinds.push([`hum-${lvl}`, (i) => lvl * (Math.sin(2 * Math.PI * 50 * i / SR) + 0.5 * Math.sin(2 * Math.PI * 150 * i / SR))]);
  kinds.push(['clicks', (i) => (i % 2600 < 40 ? (rnd() * 2 - 1) * 0.3 : (rnd() * 2 - 1) * 0.002)]);
  const chord = (i: number, tempo: number, lvl: number) => {
    const beat = Math.floor(i / (SR * 60 / tempo));
    const roots = [220, 196, 174.6, 196, 261.6, 246.9];
    const f = roots[beat % roots.length];
    const env = Math.exp(-((i % (SR * 60 / tempo)) / SR) * 3);
    const tone = Math.sin(2 * Math.PI * f * i / SR) + 0.6 * Math.sin(2 * Math.PI * f * 1.26 * i / SR) + 0.5 * Math.sin(2 * Math.PI * f * 1.5 * i / SR);
    const kick = (i % (SR * 60 / tempo)) < 800 ? Math.sin(2 * Math.PI * 60 * i / SR) * 0.8 : 0;
    return lvl * (tone * env * 0.4 + kick + (rnd() * 2 - 1) * 0.05);
  };
  for (const [tempo, lvl] of [[90, 0.05], [120, 0.1], [70, 0.03]] as const) kinds.push([`music-${tempo}-${lvl}`, (i) => chord(i, tempo, lvl)]);
  kinds.push(['silence', () => 0]);
  for (const [name, gen] of kinds) {
    for (let rep = 0; rep < 4; rep++) {
      const pcm = new Float32Array(WIN);
      for (let i = 0; i < WIN; i++) pcm[i] = gen(i + rep * WIN, WIN);
      const g = gateWindows(pcm)[0];
      yield { src: `nonspeech:${name}`, track: String(rep), pcm, gate: g?.gate ?? false, ref: [], offsetSec: 0 };
    }
  }
}

async function collect() {
  const set = arg('set', 'unnamed')!;
  const out = arg('out')!;
  const textOut = arg('text-out');
  writeFileSync(out, '');
  if (textOut) writeFileSync(textOut, '', { mode: 0o600 });
  const gen = arg('tapes') ? tapeWindows(arg('tapes')!, arg('match'), Number(arg('max-windows', '40')))
    : arg('fixture') ? fixtureWindows(arg('fixture')!) : nonspeechWindows();
  let n = 0;
  for (const w of gen) {
    const d = await stt(w.pcm);
    const segs = (d.segments ?? []).map((s: any, i: number) => {
      const a = Math.max(0, Math.floor((s.start ?? 0) * SR)), b = Math.min(w.pcm.length, Math.ceil((s.end ?? 0) * SR));
      const segRms = b > a ? rmsOf(w.pcm, a, b) : 0;
      const nw = words(s.text).length;
      const wp = (s.words ?? []).map((x: any) => x.probability).filter((x: any) => typeof x === 'number');
      const wprob = wp.length ? +(wp.reduce((a: number, b: number) => a + b, 0) / wp.length).toFixed(4) : undefined;
      const existingKeep = !isLowConfidenceSegment(s) && !(b > a && segRms < 0.004) && !isHallucination(s.text ?? '', { voiced: w.gate });
      if (textOut) appendFileSync(textOut, JSON.stringify({ set, src: w.src, track: w.track, at: w.offsetSec, i, text: s.text }) + '\n');
      return { i, start: s.start, end: s.end, avg_logprob: s.avg_logprob, no_speech_prob: s.no_speech_prob,
        compression_ratio: s.compression_ratio, wprob, segRms: +segRms.toFixed(5), nw, existingKeep,
        hyp: w.ref ? words(s.text) : undefined };
    });
    const row = { set, src: w.src, track: w.track, at: w.offsetSec, gate: w.gate, winRms: +rmsOf(w.pcm).toFixed(5),
      language: d.language, language_probability: d.language_probability, ref: w.ref, segs };
    appendFileSync(out, JSON.stringify(row) + '\n');
    n++;
  }
  console.error(`[confidence] ${set}: ${n} windows → ${out}`);
}

/** Word recall of hyp against ref (multiset, order-free). */
function matched(ref: string[], hyp: string[]): number {
  const bag = new Map<string, number>();
  for (const w of hyp) bag.set(w, (bag.get(w) ?? 0) + 1);
  let m = 0;
  for (const w of ref) { const c = bag.get(w) ?? 0; if (c > 0) { m++; bag.set(w, c - 1); } }
  return m;
}

function report(files: string[]) {
  const rows = files.filter((f) => !f.endsWith('.text.jsonl')).flatMap((f) => readFileSync(f, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)));
  const rules: [string, (s: any) => boolean][] = Object.entries(CANDIDATES);
  const groups = new Map<string, any[]>();
  for (const r of rows) {
    const kind = r.set.startsWith('nonspeech') ? 'nonspeech' : r.ref ? 'fixture' : 'tape';
    const lang = kind === 'nonspeech' ? '*' : (r.language ?? '?');
    const k = `${r.set}|${kind}|${lang}|gate=${r.gate}`;
    (groups.get(k) ?? groups.set(k, []).get(k)!).push(r);
  }
  const out: any[] = [];
  for (const [k, rs] of groups) {
    const [set, kind, lang, gate] = k.split('|');
    const segs = rs.flatMap((r: any) => r.segs.filter((s: any) => s.existingKeep).map((s: any) => ({ ...s, r })));
    const line: any = { set, kind, lang, gate, windows: rs.length, segsKeptByExisting: segs.length, wordsKeptByExisting: segs.reduce((a: number, s: any) => a + s.nw, 0) };
    for (const [name, rule] of rules) {
      const dropped = segs.filter((s: any) => rule(s));
      const res: any = { droppedSegs: dropped.length, droppedWords: dropped.reduce((a: number, s: any) => a + s.nw, 0) };
      if (kind === 'fixture') {
        let ref = 0, mOff = 0, mOn = 0;
        for (const r of rs) {
          const kept = r.segs.filter((s: any) => s.existingKeep);
          ref += r.ref.length;
          mOff += matched(r.ref, kept.flatMap((s: any) => s.hyp ?? []));
          mOn += matched(r.ref, kept.filter((s: any) => !rule(s)).flatMap((s: any) => s.hyp ?? []));
        }
        Object.assign(res, { refWords: ref, recallOff: +(mOff / Math.max(1, ref)).toFixed(4), recallOn: +(mOn / Math.max(1, ref)).toFixed(4) });
      }
      line[name] = res;
    }
    out.push(line);
  }
  console.log(JSON.stringify(out, null, 1));
}

if (arg('report')) report(process.argv.slice(process.argv.indexOf('--report') + 1).filter((a) => !a.startsWith('--')));
else void collect();

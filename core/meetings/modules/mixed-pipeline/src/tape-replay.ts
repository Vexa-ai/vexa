/**
 * tape-replay — replay a REAL captured-signal.v1 mixed-lane tape (audio frames + platform
 * "who is lit" hints) through the REAL ChunkedTranscriber, offline: injected segmenter, stub
 * STT, no model, no network, no server. Built for the first real Teams tape (meeting 24,
 * 2 speakers, 2013 records / 569 hints) to measure the two defects it exposed:
 *
 *   DUPLICATION  — how many DURABLE rows the collector would hold (upsert on
 *                  (meeting_id, segment_id)) versus how many distinct utterances exist. A
 *                  draft published under its own id becomes a second row for the same text.
 *   ATTRIBUTION  — which name each turn ends up under, scored against ground-truth windows
 *                  the humans in the meeting attested to.
 *
 * Segmentation is NOT re-derived here. The turn windows come from the live run itself
 * (--turns, `turn:{N}:{i}` segment ids grouped from the transcriptions table), so the
 * segmenter is replayed exactly as it actually cut, and the only thing under test is the
 * naming/publish path. That is deliberate: it isolates the defect from pyannote's variance.
 *
 *   npx tsx src/tape-replay.ts --tape <captured-signal.jsonl> --turns <turns.psv> [--gt <gt.json>]
 */
import { readFileSync } from 'node:fs';
import { ChunkedTranscriber, type BoundarySource } from './index.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';

const arg = (name: string): string | undefined => {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : undefined;
};
const TAPE = arg('tape');
const TURNS = arg('turns');
if (!TAPE || !TURNS) { console.error('usage: tsx src/tape-replay.ts --tape <jsonl> --turns <psv> [--gt <json>]'); process.exit(2); }

interface Frame { ts: number; pcm: string }
interface Hint { t: number; name: string; isEnd: boolean }

// ── Load the tape. A tape may carry MORE THAN ONE header (the capture restarted mid-session);
// every non-header line is a record, and records are either audio frames or hints. ──
function loadTape(path: string): { frames: Frame[]; hints: Hint[] } {
  const frames: Frame[] = [];
  const hints: Hint[] = [];
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    if (!line) continue;
    let r: any;
    try { r = JSON.parse(line); } catch { continue; }
    if (r.type === 'captured_signal_header') continue;
    if (r.type === 'hint') { if (r.name) hints.push({ t: r.t, name: r.name, isEnd: !!r.isEnd }); continue; }
    if (typeof r.pcm === 'string' && typeof r.ts === 'number') frames.push({ ts: r.ts, pcm: r.pcm });
  }
  return { frames, hints };
}

function loadTurns(path: string): { turn: number; t0: number; t1: number }[] {
  return readFileSync(path, 'utf8').split('\n').filter(Boolean).map((l) => {
    const [turn, t0, t1] = l.split('|');
    return { turn: Number(turn), t0: Number(t0), t1: Number(t1) };
  }).filter((t) => Number.isFinite(t.t0) && Number.isFinite(t.t1)).sort((a, b) => a.t0 - b.t0);
}

const pcmOf = (f: Frame): Float32Array => {
  const b = Buffer.from(f.pcm, 'base64');
  return new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
};

async function main(): Promise<void> {
  const { frames, hints } = loadTape(TAPE!);
  const turns = loadTurns(TURNS!);
  console.log(`tape: ${frames.length} audio frame(s), ${hints.length} hint(s); replaying ${turns.length} live turn window(s)`);

  let emit!: (ev: BoundaryEvent) => void;
  /** Every publish call, in order — the naive "one row per publish" view. */
  const writes: { id: string; speaker: string; text: string; completed: boolean }[] = [];
  /** The collector's durable view: UPSERT on segment id (its unique key with meeting_id). */
  const durable = new Map<string, { speaker: string; text: string }>();
  const renames: { from: string; to: string; n: number }[] = [];

  const tc = await ChunkedTranscriber.create({
    language: 'en',
    // Stub STT: one marker word per second of submitted audio. Text quality is not under
    // test — but the words must GROW BY A STABLE PREFIX as each pass resubmits a longer
    // span, or LocalAgreement never confirms anything and the draft/confirm transition
    // (the whole point of the duplication measurement) never happens.
    transcribe: async (pcm: Float32Array) => {
      const secs = Math.max(1, Math.floor(pcm.length / 16000));
      const text = Array.from({ length: secs }, (_, i) => `w${i}`).join(' ');
      return {
        text, language: 'en', language_probability: 0.99, duration: pcm.length / 16000,
        segments: [{ text, start: 0, end: pcm.length / 16000, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any],
      };
    },
    publish: (speaker, confirmed, pending) => {
      for (const c of confirmed) { writes.push({ id: c.segmentId, speaker, text: c.text, completed: true }); durable.set(c.segmentId, { speaker, text: c.text }); }
      for (const p of pending ?? []) { writes.push({ id: p.segmentId, speaker, text: p.text, completed: false }); durable.set(p.segmentId, { speaker, text: p.text }); }
    },
    publishPending: (speaker, segs) => {
      for (const p of segs) { writes.push({ id: p.segmentId, speaker, text: p.text, completed: false }); durable.set(p.segmentId, { speaker, text: p.text }); }
    },
    clearPending: () => {},
    rename: (oldS, newS, segs) => {
      renames.push({ from: oldS, to: newS, n: segs.length });
      for (const s of segs) { writes.push({ id: s.segmentId, speaker: newS, text: s.text, completed: true }); durable.set(s.segmentId, { speaker: newS, text: s.text }); }
    },
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
      emit = onBoundary;
      return { appendFrame: async () => {}, reset: () => {} };
    },
    log: () => {},
  });

  // ── Drive the tape in TIMESTAMP order across all three streams: audio, hints, and the
  // recorded turn boundaries. Each carries its own capture ts, so no wall-clock pacing is
  // needed and the replay stays deterministic. ──
  type Ev = { t: number; run: () => void };
  const evs: Ev[] = [];
  for (const f of frames) evs.push({ t: f.ts, run: () => tc.feedAudio(pcmOf(f), f.ts) });
  for (const h of hints) evs.push({ t: h.t, run: () => tc.recordHint(h.name, 'dom-outline', h.t, h.isEnd) });
  for (const tn of turns) {
    evs.push({ t: tn.t0, run: () => emit({ tMs: tn.t0, kind: 'silence→speaker', confidence: 0.9 }) });
    evs.push({ t: tn.t1, run: () => emit({ tMs: tn.t1, kind: 'speaker→silence', confidence: 0.9 }) });
  }
  evs.sort((a, b) => a.t - b.t);
  // PACING MATTERS. The transcriber's submit pump is driven by a WALL-CLOCK 1s tick, so a turn
  // is transcribed in several growing passes live but in ONE pass if the tape is fired instantly.
  // Attribution defects that live in the CADENCE — a name locked on the first tick, when the
  // commit window is a fraction of a second — are invisible without it. `--realtime` replays the
  // tape at its captured rate (1x; ~7 min for m24); without it only structure/duplication is
  // meaningful.
  const realtime = process.argv.includes('--realtime');
  const t0 = evs[0]?.t ?? 0;
  const wall0 = Date.now();
  for (let i = 0; i < evs.length; i++) {
    if (realtime) {
      const due = wall0 + (evs[i].t - t0);
      const wait = due - Date.now();
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    }
    evs[i].run();
    if (!realtime && i % 200 === 0) await new Promise((r) => setImmediate(r));   // let the async pump drain
  }
  await tc.dispose();

  // ── DUPLICATION ──
  const texts = new Set([...durable.values()].map((v) => v.text));
  console.log(`\nDUPLICATION`);
  console.log(`  publish calls (naive one-row-per-publish): ${writes.length}`);
  console.log(`  durable rows after UPSERT on segment_id  : ${durable.size}`);
  console.log(`  distinct texts among durable rows        : ${texts.size}`);
  const dupIds = [...durable.keys()].filter((id) => /:p\d+$/.test(id));
  console.log(`  durable rows carrying a DRAFT-ONLY id (:pN): ${dupIds.length}`);
  const perTurn = new Map<string, Set<string>>();
  for (const id of durable.keys()) {
    const m = /^turn:(\d+):(p?\d+)$/.exec(id);
    if (!m) continue;
    if (!perTurn.has(m[1])) perTurn.set(m[1], new Set());
    perTurn.get(m[1])!.add(m[2]);
  }
  const shadowed = [...perTurn.entries()].filter(([, s]) => [...s].some((x) => x.startsWith('p'))).length;
  console.log(`  turns holding BOTH a draft id and a confirmed id: ${shadowed}`);

  // ── ATTRIBUTION ──
  const byTime: { t: number; speaker: string }[] = [];
  for (const [id, v] of durable) {
    const m = /^turn:(\d+):/.exec(id);
    const tn = m ? turns.find((x) => x.turn === Number(m[1])) : undefined;
    if (tn) byTime.push({ t: tn.t0, speaker: v.speaker });
  }
  byTime.sort((a, b) => a.t - b.t);
  const tally = new Map<string, number>();
  for (const r of byTime) tally.set(r.speaker, (tally.get(r.speaker) ?? 0) + 1);
  console.log(`\nATTRIBUTION`);
  console.log(`  label distribution: ${JSON.stringify(Object.fromEntries(tally))}`);
  console.log(`  renames issued: ${renames.length}`);

  const gtPath = arg('gt');
  if (gtPath) {
    const gt = JSON.parse(readFileSync(gtPath, 'utf8')) as { label: string; t0: number; t1: number; who: string }[];
    let right = 0, wrong = 0, unknown = 0;
    for (const w of gt) {
      const inWin = byTime.filter((r) => r.t >= w.t0 && r.t < w.t1);
      const c = { right: 0, wrong: 0, unknown: 0 };
      for (const r of inWin) {
        if (r.speaker === w.who) c.right++;
        else if (r.speaker === 'Speaker' || /^seg_\d+$/.test(r.speaker)) c.unknown++;
        else c.wrong++;
      }
      right += c.right; wrong += c.wrong; unknown += c.unknown;
      console.log(`  ${w.label} (truth: ${w.who}) → right ${c.right} · wrong ${c.wrong} · unattributed ${c.unknown}`);
    }
    const scored = right + wrong;
    console.log(`  TOTAL over ground truth: right ${right} · wrong ${wrong} · unattributed ${unknown}` +
      (scored ? `  (accuracy over attributed: ${((right / scored) * 100).toFixed(1)}%)` : ''));
  }
}

void main().catch((e) => { console.error('tape-replay failed:', e); process.exit(1); });

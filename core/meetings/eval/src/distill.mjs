#!/usr/bin/env node
// distill — cut a recorded captured-signal.v1 session down to a MINIMAL replay fixture.
//
// The failure→fixture step of the harvest loop: a live bug is observed on a recorded
// session; distill the offending time window (± padding) into a small self-contained
// fixture that replays through the exact pipeline offline (services/bot/src/replay.test.ts
// consumes it verbatim; eval/src/replay.mjs re-sends it into a live desktop ingest).
//
//   node distill.mjs <session.jsonl> [--from <epoch-ms|ISO>] [--to <epoch-ms|ISO>]
//                    [--speaker <name>] [--pad <ms>] [--out <fixture.jsonl>]
//
//   --from/--to   keep frames whose ts falls in [from, to] (default: whole session)
//   --speaker     keep only frames named/hinted <name> (attribution repros)
//   --pad         widen the window by <ms> both sides (default 2000 — context the
//                 segmenter needs around the symptom)
//   --out         output path (default: <session>.distilled.jsonl)
//
// Frames are re-seq'd from 0; ts values are NEVER restamped (the pipeline's segmentation
// clock is the capture ts). Prints a summary (frames kept/dropped, speakers, span) so the
// distilled fixture is a checkable claim, not a silent slice.
import fs from 'node:fs';
import { gunzipSync } from 'node:zlib';

const args = process.argv.slice(2);
const input = args.find((a) => !a.startsWith('--'));
if (!input) {
  console.error('usage: node distill.mjs <session.jsonl> [--from t] [--to t] [--speaker name] [--pad ms] [--out path]');
  process.exit(2);
}
const opt = (name, dflt) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : dflt;
};
const parseT = (v) => {
  if (v === undefined) return undefined;
  const n = Number(v);
  if (Number.isFinite(n)) return n;
  const d = Date.parse(v);
  if (Number.isFinite(d)) return d;
  console.error(`distill: cannot parse time "${v}" (epoch ms or ISO)`);
  process.exit(2);
};

const pad = Number(opt('pad', '2000'));
const speaker = opt('speaker', undefined);
const out = opt('out', input.replace(/\.jsonl$/, '') + '.distilled.jsonl');

const raw = input.endsWith('.gz') ? gunzipSync(fs.readFileSync(input)).toString('utf8') : fs.readFileSync(input, 'utf8');
const lines = raw.split('\n').filter(Boolean);
const header = JSON.parse(lines[0]);
if (header.type !== 'captured_signal_header') {
  console.error('distill: not a captured-signal.v1 session (bad header)');
  process.exit(2);
}
// A session is a header + records: audio CapturedFrames and `type:"hint"` HintEvents. The mixed
// lane's attribution lives ENTIRELY in the hints, so a window must carry both or the distilled
// fixture reproduces sound without speakers.
const records = lines.slice(1).map((l) => JSON.parse(l));
const isHint = (r) => r.type === 'hint';
const isBoundary = (r) => r.type === 'boundary';
const isAudio = (r) => !isHint(r) && !isBoundary(r);
// Hints carry `t`, cuts carry `tMs`, audio carries `ts` — one clock, three field names.
const timeOf = (r) => (isHint(r) ? r.t : isBoundary(r) ? r.tMs : r.ts);

const from = (parseT(opt('from', undefined)) ?? -Infinity) - pad;
const to = (parseT(opt('to', undefined)) ?? Infinity) + pad;

let kept = records.filter((r) => timeOf(r) >= from && timeOf(r) <= to);
if (speaker) {
  // gmeet names each frame (glow-bound), so a name filter selects frames directly. The mixed
  // lane carries ONE stream nobody's name is on — there the speaker lives only in the hints, so
  // select the TIME WINDOWS that speaker is hinted over and keep the audio inside them.
  const named = kept.filter((r) => isAudio(r) && (r.speakerName === speaker || r.hint === speaker));
  if (named.length) {
    kept = kept.filter((r) => (isHint(r) ? r.name === speaker : isBoundary(r) ? true : named.includes(r)));
  } else {
    const windows = [];
    let open = null;
    for (const r of kept.filter(isHint).sort((a, b) => a.t - b.t)) {
      if (r.name === speaker && !r.isEnd) { open = open ?? r.t; continue; }
      if (open !== null && (r.isEnd ? r.name === speaker : r.name !== speaker)) {
        windows.push([open, r.t]); open = null;
      }
    }
    if (open !== null) windows.push([open, Infinity]);
    if (!windows.length) {
      console.error(`distill: no hint names "${speaker}" — cannot derive a window in the mixed lane`);
      process.exit(1);
    }
    const inWin = (t) => windows.some(([a, b]) => t >= a - pad && t <= b + pad);
    kept = kept.filter((r) => (isHint(r) ? r.name === speaker : inWin(timeOf(r))));
    console.log(`  (mixed lane: "${speaker}" derived from ${windows.length} hint window(s))`);
  }
}
// Re-seq the audio frames from 0; hints keep their own clock (they are matched by time, not order).
let n = 0;
kept = kept.map((r) => (isAudio(r) ? { ...r, seq: n++ } : r));

const keptFrames = kept.filter(isAudio);
const keptHints = kept.filter(isHint);
if (keptFrames.length === 0) {
  console.error('distill: window/filter matched ZERO audio frames — nothing written');
  process.exit(1);
}

fs.writeFileSync(out, [JSON.stringify(header), ...kept.map((r) => JSON.stringify(r))].join('\n') + '\n', 'utf8');

const names = [...new Set(kept.flatMap((r) => (isHint(r) ? [r.name] : [r.speakerName, r.hint])).filter(Boolean))];
const t0 = timeOf(kept[0]);
const t1 = timeOf(kept[kept.length - 1]);
const span = ((t1 - t0) / 1000).toFixed(1);
const totalFrames = records.filter(isAudio).length;
const totalHints = records.filter(isHint).length;
const keptCuts = kept.filter(isBoundary).length;
const totalCuts = records.filter(isBoundary).length;
console.log(`distilled ${keptFrames.length}/${totalFrames} frames + ${keptHints.length}/${totalHints} hints + ${keptCuts}/${totalCuts} cuts → ${out}`);
console.log(`  span ${span}s (${new Date(t0).toISOString()} → ${new Date(t1).toISOString()})`);
console.log(`  speakers/hints: ${names.join(', ') || '(none)'}   lane=${header.lane ?? '?'} platform=${header.platform}`);
if (header.lane === 'mixed' && totalCuts > 0 && keptCuts === 0) {
  console.log('  ⚠ mixed lane with recorded cuts, but NONE in window — the replay will have to invent its chunking');
}
if (header.lane === 'mixed' && keptHints.length === 0) {
  console.log('  ⚠ mixed lane with NO hints in window — a replay of this fixture cannot attribute speakers');
}
console.log(`  replay offline:  (from meetings/services/bot)  REPLAY_FIXTURE=${out} npx tsx src/replay.test.ts`);
console.log(`  replay live:     node eval/src/replay.mjs ${out}`);

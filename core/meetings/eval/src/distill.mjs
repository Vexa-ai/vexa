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

const lines = fs.readFileSync(input, 'utf8').split('\n').filter(Boolean);
const header = JSON.parse(lines[0]);
if (header.type !== 'captured_signal_header') {
  console.error('distill: not a captured-signal.v1 session (bad header)');
  process.exit(2);
}
const frames = lines.slice(1).map((l) => JSON.parse(l));

const from = (parseT(opt('from', undefined)) ?? -Infinity) - pad;
const to = (parseT(opt('to', undefined)) ?? Infinity) + pad;

let kept = frames.filter((f) => f.ts >= from && f.ts <= to);
if (speaker) kept = kept.filter((f) => f.speakerName === speaker || f.hint === speaker);
kept = kept.map((f, i) => ({ ...f, seq: i }));

if (kept.length === 0) {
  console.error('distill: window/filter matched ZERO frames — nothing written');
  process.exit(1);
}

fs.writeFileSync(out, [JSON.stringify(header), ...kept.map((f) => JSON.stringify(f))].join('\n') + '\n', 'utf8');

const names = [...new Set(kept.flatMap((f) => [f.speakerName, f.hint].filter(Boolean)))];
const span = ((kept[kept.length - 1].ts - kept[0].ts) / 1000).toFixed(1);
console.log(`distilled ${kept.length}/${frames.length} frames → ${out}`);
console.log(`  span ${span}s (${new Date(kept[0].ts).toISOString()} → ${new Date(kept[kept.length - 1].ts).toISOString()})`);
console.log(`  speakers/hints: ${names.join(', ') || '(none)'}   lane=${header.lane ?? '?'} platform=${header.platform}`);
console.log(`  replay offline:  (from meetings/services/bot)  REPLAY_FIXTURE=${out} npx tsx src/replay.test.ts`);
console.log(`  replay live:     node eval/src/replay.mjs ${out}`);

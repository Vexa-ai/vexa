/**
 * Diagnostic: for a given corpus, print every GT speaker change point
 * and the distance to its nearest diarizer commit boundary. Used to see
 * which boundaries the diarizer is missing and by how far.
 */
import { promises as fs } from 'fs';

const corpusId = process.argv[2];
if (!corpusId) { console.error('usage: tsx inspect-boundaries.ts <corpusId>'); process.exit(1); }
const dir = '/home/dima/dev/vexa-pack-pack-msteams-local-diarization-rnd/services/vexa-bot/rnd/diarization/eval/corpus';
const score = JSON.parse(await fs.readFile(`${dir}/${corpusId}.transcript-score.json`, 'utf-8'));
const gt = JSON.parse(await fs.readFile(`${dir}/${corpusId}.ground-truth.json`, 'utf-8'));
const boundaries: number[] = score.commitScores.map((c: any) => c.tStartMs).sort((a: number, b: number) => a - b);
const changes: Array<{ ts: number; from: string; to: string }> = [];
for (let i = 1; i < gt.turns.length; i++) {
  if (gt.turns[i].speaker !== gt.turns[i - 1].speaker) {
    changes.push({ ts: gt.turns[i].start_ms, from: gt.turns[i - 1].speaker, to: gt.turns[i].speaker });
  }
}
console.log(`Corpus: ${corpusId}`);
console.log(`GT changes: ${changes.length}`);
console.log(`Commit boundaries: ${boundaries.length}`);
console.log();
let hits500 = 0, hits750 = 0, hits1000 = 0, misses = 0;
for (const ch of changes) {
  let minAbs = Infinity;
  let nearestSigned = 0;
  for (const b of boundaries) {
    const d = b - ch.ts;
    if (Math.abs(d) < minAbs) { minAbs = Math.abs(d); nearestSigned = d; }
  }
  if (minAbs <= 500) hits500++;
  else if (minAbs <= 750) hits750++;
  else if (minAbs <= 1000) hits1000++;
  else misses++;
  const status = minAbs <= 500 ? '✓ 500ms' : minAbs <= 750 ? '~ 750ms' : minAbs <= 1000 ? '~ 1s' : '✗ MISS';
  const sign = nearestSigned >= 0 ? '+' : '';
  console.log(`  ${(ch.ts/1000).toFixed(2).padStart(7)}s  ${ch.from} → ${ch.to}  nearest=${sign}${nearestSigned}ms  ${status}`);
}
console.log();
console.log(`Summary: ${hits500}/${changes.length} within 500ms, +${hits750} within 750ms, +${hits1000} within 1s, ${misses} truly missed`);

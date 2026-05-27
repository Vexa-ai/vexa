/**
 * Side-by-side comparison: ground-truth turns vs diarizer commits.
 *
 * Reads:
 *   eval/corpus/<id>.ground-truth.json
 *   eval/corpus/<id>.harness-output.json
 * Prints a unified timeline with one row per event (turn OR commit), sorted
 * by time, so you can eyeball whether the diarizer's labels track the
 * ground-truth speaker transitions.
 *
 * No DER computation. Manual visual diff per the MVP2 minimum-viable plan.
 */

import { promises as fs } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CORPUS_DIR = path.join(__dirname, 'corpus');

interface GroundTruthTurn {
  speaker: string;
  text: string;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
}

interface HarnessCommit {
  speakerId: string;
  tStartMs: number;
  tEndMs: number;
  centroidDist: number;
  turnDist: number;
  isNew: boolean;
  dbSize: number;
  seedAllowed: boolean;
}

function pad(s: string, n: number): string {
  if (s.length >= n) return s.slice(0, n);
  return s + ' '.repeat(n - s.length);
}

function fmtTime(ms: number): string {
  const s = ms / 1000;
  return s.toFixed(2).padStart(6) + 's';
}

async function main(): Promise<number> {
  const idArg = process.argv[2];
  if (!idArg) {
    console.error('usage: tsx eval/print-comparison.ts <conversation-id>');
    return 2;
  }
  const gt = JSON.parse(
    await fs.readFile(path.join(CORPUS_DIR, `${idArg}.ground-truth.json`), 'utf-8'),
  ) as { turns: GroundTruthTurn[]; total_duration_ms: number };
  const ho = JSON.parse(
    await fs.readFile(path.join(CORPUS_DIR, `${idArg}.harness-output.json`), 'utf-8'),
  ) as { commits: HarnessCommit[]; total_duration_ms: number; diarizer_name: string };

  // Print header
  console.log();
  console.log(`Conversation: ${idArg}`);
  console.log(`Duration: ${(gt.total_duration_ms / 1000).toFixed(2)}s`);
  console.log(`Diarizer: ${ho.diarizer_name}`);
  console.log(`Ground truth: ${gt.turns.length} turns`);
  console.log(`Harness commits: ${ho.commits.length}`);
  console.log();

  // Print ground truth column
  console.log('GROUND TRUTH (synthetic conversation script)');
  console.log('─'.repeat(96));
  console.log(`  ${pad('start', 8)}  ${pad('end', 8)}  ${pad('speaker', 12)}  text`);
  console.log('─'.repeat(96));
  for (const t of gt.turns) {
    const text = t.text.length > 60 ? t.text.slice(0, 57) + '...' : t.text;
    console.log(`  ${fmtTime(t.start_ms)}  ${fmtTime(t.end_ms)}  ${pad(t.speaker, 12)}  ${text}`);
  }
  console.log();

  // Print harness output column
  console.log('HARNESS DIARIZER COMMITS');
  console.log('─'.repeat(96));
  console.log(
    `  ${pad('start', 8)}  ${pad('end', 8)}  ${pad('label', 12)}  ` +
      `${pad('centroid_d', 11)}  ${pad('turn_d', 7)}  ${pad('flags', 18)}`,
  );
  console.log('─'.repeat(96));
  for (const c of ho.commits) {
    const flags: string[] = [];
    if (c.isNew) flags.push('NEW');
    flags.push(`db=${c.dbSize}`);
    if (!c.seedAllowed) flags.push('!seed');
    console.log(
      `  ${fmtTime(c.tStartMs)}  ${fmtTime(c.tEndMs)}  ${pad(c.speakerId, 12)}  ` +
        `${pad(Number.isFinite(c.centroidDist) ? c.centroidDist.toFixed(3) : '   --', 11)}  ` +
        `${pad(Number.isFinite(c.turnDist) ? c.turnDist.toFixed(3) : '  --', 7)}  ${pad(flags.join(' '), 18)}`,
    );
  }
  console.log();

  // Unified timeline view — interleave ground truth and harness commits
  console.log('UNIFIED TIMELINE  (ground truth = GT, harness commit = HX)');
  console.log('─'.repeat(96));
  type Ev = { t: number; kind: 'GT' | 'HX'; line: string };
  const events: Ev[] = [];
  for (const t of gt.turns) {
    events.push({
      t: t.start_ms,
      kind: 'GT',
      line: `${pad('GT', 4)} ${fmtTime(t.start_ms)}–${fmtTime(t.end_ms)}  ${pad(t.speaker, 10)}  ${t.text.slice(0, 50)}`,
    });
  }
  for (const c of ho.commits) {
    events.push({
      t: c.tStartMs,
      kind: 'HX',
      line: `${pad('HX', 4)} ${fmtTime(c.tStartMs)}–${fmtTime(c.tEndMs)}  ${pad(c.speakerId, 10)}  ` +
        `c_d=${Number.isFinite(c.centroidDist) ? c.centroidDist.toFixed(3) : '--'}  ` +
        `t_d=${Number.isFinite(c.turnDist) ? c.turnDist.toFixed(3) : '--'}  ` +
        (c.isNew ? 'NEW  ' : '     ') +
        (c.seedAllowed ? '' : '!seed'),
    });
  }
  events.sort((a, b) => a.t - b.t);
  for (const e of events) console.log('  ' + e.line);
  console.log();

  // Quick alignment summary — try to pair each GT turn with the harness
  // commit whose midpoint sits inside the GT range (best naive match).
  console.log('NAIVE GT ↔ HARNESS ALIGNMENT (per-turn label assignment)');
  console.log('─'.repeat(96));
  const assignments = new Map<string, Set<string>>();
  for (const t of gt.turns) {
    let bestCommit: HarnessCommit | null = null;
    let bestOverlap = -1;
    for (const c of ho.commits) {
      const overlap = Math.max(0, Math.min(t.end_ms, c.tEndMs) - Math.max(t.start_ms, c.tStartMs));
      if (overlap > bestOverlap) {
        bestOverlap = overlap;
        bestCommit = c;
      }
    }
    const label = bestCommit ? bestCommit.speakerId : '(no overlap)';
    console.log(`  ${pad(t.speaker, 12)} → ${pad(label, 12)}  (${t.text.slice(0, 50)})`);
    if (!assignments.has(t.speaker)) assignments.set(t.speaker, new Set());
    if (bestCommit) assignments.get(t.speaker)!.add(bestCommit.speakerId);
  }
  console.log();
  console.log('Per-ground-truth-speaker → set of assigned diarizer labels:');
  for (const [spk, labels] of assignments) {
    const ok = labels.size === 1 ? '✓ consistent' : `✗ split across ${labels.size} clusters`;
    console.log(`  ${pad(spk, 12)} → {${[...labels].join(', ')}}  ${ok}`);
  }
  console.log();

  return 0;
}

main().then((code) => process.exit(code)).catch((err) => {
  console.error('[print-comparison] fatal:', err);
  process.exit(1);
});

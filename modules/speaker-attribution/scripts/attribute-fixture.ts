#!/usr/bin/env tsx
/**
 * attribute-fixture — run the MIXED speaker-attribution strategy over a recorded
 * pipeline output, with NO live meeting.
 *
 *   tsx scripts/attribute-fixture.ts <fixture-dir>
 *
 * Reads:  <dir>/separated-transcript.v1.jsonl  (cluster-keyed segments)
 *         <dir>/stream.capture                 (capture.v1 wire log — for the active-speaker hints)
 * Writes: <dir>/transcript.v1.jsonl            (segments with RESOLVED names)
 *
 * This is the third brick in the chain proven offline against the fixture the
 * pipeline produced — capture → pipeline → speaker-attribution, no meeting.
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { attributeMixed } from '../src/index';
import type { HintEvent } from '../src/cluster-name-binder';
import type { SeparatedSegment } from '../src/contracts/separated-transcript-v1';
import type { TranscriptSegment } from '../src/contracts/transcript-v1';

const dir = process.argv[2];
if (!dir || !existsSync(dir)) { console.error('usage: tsx scripts/attribute-fixture.ts <fixture-dir>'); process.exit(1); }

// separated-transcript.v1 segments (the pipeline's output golden)
const segments: SeparatedSegment[] = readFileSync(join(dir, 'separated-transcript.v1.jsonl'), 'utf8')
  .trim().split('\n').filter(Boolean).map((l) => JSON.parse(l));

// active-speaker hints out of the faithful capture.v1 stream log
const buf = readFileSync(join(dir, 'stream.capture'));
const hints: HintEvent[] = [];
let off = 0;
while (off + 5 <= buf.length) {
  const type = buf.readUInt8(off); const len = buf.readUInt32LE(off + 1); off += 5;
  const payload = buf.subarray(off, off + len); off += len;
  if (type !== 1) continue;
  try {
    const ev = JSON.parse(payload.toString('utf8'));
    if (ev.kind === 'active-speaker' && ev.speaker) {
      hints.push({ name: String(ev.speaker), tMs: Number(ev.ts), kind: (ev.detail?.hint as any) || 'dom-active', isEnd: !!ev.detail?.isEnd });
    }
  } catch { /* skip */ }
}
console.log(`▶ ${segments.length} segments, ${hints.length} active-speaker hints`);

const out: TranscriptSegment[] = [];
attributeMixed(segments, {
  hints,
  sink: { segment: (s) => out.push(s), finalize: () => {} },
  log: (m) => console.log(`  ${m}`),
});

writeFileSync(join(dir, 'transcript.v1.jsonl'), out.map((s) => JSON.stringify(s)).join('\n') + '\n');

// report
const bySpeaker: Record<string, number> = {};
const bySource: Record<string, number> = {};
for (const s of out) { bySpeaker[s.speaker] = (bySpeaker[s.speaker] || 0) + 1; bySource[s.source] = (bySource[s.source] || 0) + 1; }
console.log(`\n── transcript.v1 (${out.length} segments) ──`);
console.log('by speaker:', JSON.stringify(bySpeaker));
console.log('by binding source:', JSON.stringify(bySource));
console.log('\nsample:');
for (const s of out.slice(0, 12)) console.log(`  [${s.speaker}] (${s.source}) ${s.text.slice(0, 70)}`);
console.log(`\n→ ${join(dir, 'transcript.v1.jsonl')}`);

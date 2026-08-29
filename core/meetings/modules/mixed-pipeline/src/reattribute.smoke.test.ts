/**
 * reattribute.smoke — an OPEN turn keeps re-resolving as its window grows; only CLOSING
 * locks the name.
 *
 * The defect this pins (m24, the first real Teams tape). Attribution used to be sticky on the
 * FIRST tick that produced any name at all: `if (turn.resolvedName) return turn.resolvedName`.
 * A submit tick fires roughly every second, so the very first one decides a turn whose commit
 * window is still a fraction of a second — whoever's tile happened to be lit at the onset owned
 * the whole utterance. On Teams the tile lights on NOISE, so the other participant TYPING at the
 * moment a turn began stamped his name on all of it. Measured on the tape: across the ground-truth
 * window where Dmitry describes the teams-capture module, the hints name Dmitry for 145.4s of lit
 * time against Jacob's 47.2s — better than 3:1 — and the transcript still said Jacob throughout.
 *
 * The rule now: while the turn is OPEN, re-resolve over the grown window and let a challenger take
 * it only on a clear majority of the evidence so far; at close, lock. This test drives the binder the way
 * the transcriber does and asserts the accumulated evidence wins, WITHOUT inventing any: a turn
 * with no dominant name still resolves to nothing.
 */
import { ClusterNameBinder } from './index.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const REATTRIBUTE_MIN_CONFIDENCE = Number(process.env.VEXA_REATTRIBUTE_MIN_CONFIDENCE || 0.75);

interface Hint { name: string; tMs: number; isEnd?: boolean }

/** The transcriber's open-turn policy, exercised against the real binder: hints arrive AS TIME
 *  PASSES (never all up front — that would hand the first tick evidence it could not have had),
 *  the turn is resolved at each tick over the window grown so far, and a challenger needs MARGIN
 *  over the incumbent's confidence to take it. */
function driveOpenTurn(b: ClusterNameBinder, clusterId: string, t0: number, ticks: number[], hints: Hint[] = []):
    { name: string | null; confidence: number; first: string } {
  let name: string | null = null;
  let confidence = 0;
  let first = '';
  let fed = 0;
  for (const tEnd of ticks) {
    while (fed < hints.length && hints[fed].tMs <= tEnd) {
      const h = hints[fed++];
      b.recordHint({ name: h.name, tMs: h.tMs, kind: 'dom-outline', isEnd: h.isEnd });
    }
    const r = b.resolve({ clusterId, tStartMs: t0, tEndMs: tEnd }, { recordVote: false });
    if (!first) first = `${r.speakerName} (${r.source})`;
    if (r.source === 'provisional-cluster-id') continue;
    if (name === null) { name = r.speakerName; confidence = r.confidence; continue; }
    if (r.speakerName === name) { confidence = Math.max(confidence, r.confidence); continue; }
    if (r.source === 'window-match' && r.confidence >= REATTRIBUTE_MIN_CONFIDENCE) { name = r.speakerName; confidence = r.confidence; }
  }
  return { name, confidence, first };
}

// ── 1. THE DEFECT. Jacob's tile lights on typing noise for ~2s at the turn's onset; Dmitry
// speaks for the next 20s and his tile is lit throughout (the ~2s Teams heartbeat re-asserts it).
{
  const b = new ClusterNameBinder({ kindLagMs: { 'dom-outline': 0 } });
  const hints: Hint[] = [
    { name: 'Jacob', tMs: 800 }, { name: 'Jacob', tMs: 2600, isEnd: true },
  ];
  for (let t = 2000; t <= 22000; t += 2000) hints.push({ name: 'Dmitry', tMs: t });
  hints.push({ name: 'Dmitry', tMs: 22500, isEnd: true });
  hints.sort((a, b2) => a.tMs - b2.tMs);

  // The turn opens at 1000 and the pump ticks about once a second as audio arrives.
  const out = driveOpenTurn(b, 'seg_t', 1000, [1900, 2900, 4900, 8900, 14900, 22000], hints);
  console.log(`  first tick → ${out.first}; at close → ${out.name} (conf ${out.confidence.toFixed(2)})`);
  check('the first tick locks the WRONG name (this is the defect being pinned)',
    out.first.startsWith('Jacob'), out.first);
  check('the whole turn still ends under the speaker the grown window supports (Dmitry)',
    out.name === 'Dmitry', String(out.name));
}

// ── 2. NO FABRICATION. Both tiles lit for the whole turn (the genuinely ambiguous case that IS
// platform-inherent): the turn must stay unattributed rather than pick one.
{
  const b = new ClusterNameBinder({ kindLagMs: { 'dom-outline': 0 } });
  for (let t = 1000; t <= 20000; t += 2000) {
    b.recordHint({ name: 'Jacob', tMs: t, kind: 'dom-outline' });
    b.recordHint({ name: 'Dmitry', tMs: t, kind: 'dom-outline' });
  }
  const out = driveOpenTurn(b, 'seg_amb', 1000, [2000, 6000, 12000, 20000]);
  check('two tiles lit the whole turn stays UNKNOWN (no coin-flip name)', out.name === null, String(out.name));
}

// ── 3. NO THRASH. A correctly-named turn is not taken by a later brief flicker of the other name.
{
  const b = new ClusterNameBinder({ kindLagMs: { 'dom-outline': 0 } });
  for (let t = 1000; t <= 20000; t += 2000) b.recordHint({ name: 'Dmitry', tMs: t, kind: 'dom-outline' });
  b.recordHint({ name: 'Jacob', tMs: 17000, kind: 'dom-outline' });
  b.recordHint({ name: 'Jacob', tMs: 18400, kind: 'dom-outline', isEnd: true });
  const out = driveOpenTurn(b, 'seg_stable', 1000, [3000, 8000, 14000, 20000]);
  check('a brief late flicker does not take an established turn', out.name === 'Dmitry', String(out.name));
}

if (failed) { console.error(`\n❌ reattribute: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ reattribute: an open turn follows the evidence as its window grows, stays UNKNOWN when there is none, and does not thrash.');

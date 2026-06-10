// Synthesize a 6-participant Google Meet trace (You + 5 remote speakers) with
// realistic dynamics: round-robin turns, two overlap segments, a late joiner,
// and the self tile occasionally "speaking" (must be excluded). Uses a REAL
// known indicator class (Oaajhc) so it exercises the real matching path.
import { writeFileSync } from 'node:fs';

const SELF = 'You';
const remotes = [
  { track: 0, id: 'p0', name: 'Alice Aronson' },
  { track: 1, id: 'p1', name: 'Bob Barker' },
  { track: 2, id: 'p2', name: 'Carol Chen' },
  { track: 3, id: 'p3', name: 'Dave Diaz' },
  { track: 4, id: 'p4', name: 'Erin Ito' },
];
const selfTile = { id: 'self', name: SELF, self: true };

const events = [];
let t = 0;
const IND = ['Oaajhc'];

// active = set of remote indices currently speaking
function tilesEvent(activeIdx, present = remotes.length) {
  const tiles = remotes.slice(0, present).map((r, i) => ({
    id: r.id, name: r.name, self: false,
    rootClasses: 'participant ' + (activeIdx.includes(i) ? 'Oaajhc' : ''),
    indicatorClasses: activeIdx.includes(i) ? IND : [],
  }));
  tiles.push({ id: selfTile.id, name: selfTile.name, self: true, rootClasses: 'participant', indicatorClasses: [] });
  events.push({ t, kind: 'tiles', tiles });
}

// A speaking window: tile lit + audio on its track every 100ms for `durMs`.
function speak(idx, durMs, present) {
  tilesEvent(idx, present);                    // mark active
  const end = t + durMs;
  while (t < end) { for (const i of idx) events.push({ t, kind: 'audio', track: remotes[i].track }); t += 100; }
  tilesEvent([], present);                      // clear
  t += 200;                                     // gap
}

// Phase 1: solo round-robin, only 4 present (Erin joins later)
for (const i of [0, 1, 2, 3]) speak([i], 2500, 4);
// Phase 2: overlap pairs
speak([0, 1], 1500, 4);
speak([2, 3], 1500, 4);
// Phase 3: self tile "speaks" (audio would never arrive on a remote track, so emit none) — just to ensure exclusion holds
tilesEvent([], 4); events.push({ t, kind: 'tiles', tiles: [
  ...remotes.slice(0,4).map(r => ({ id:r.id, name:r.name, self:false, rootClasses:'participant', indicatorClasses:[] })),
  { id:'self', name:SELF, self:true, rootClasses:'participant Oaajhc', indicatorClasses:IND },
]}); t += 1500;
// Phase 4: late joiner Erin (5th remote) speaks
speak([4], 2500, 5);
// Phase 5: another round so everyone gets 2nd confirmation
for (const i of [0, 1, 2, 3, 4]) speak([i], 1500, 5);

const trace = {
  version: 1, selfName: SELF, durationMs: t,
  params: { pollMs: 500, audioWindowMs: 700, lockThreshold: 2, lockRatio: 0.7 },
  events,
  finalLocks: { 0: 'Alice Aronson', 1: 'Bob Barker', 2: 'Carol Chen', 3: 'Dave Diaz', 4: 'Erin Ito' },
};
writeFileSync('fixtures/multi-speaker-6.trace.json', JSON.stringify(trace, null, 0));
console.log(`generated 6-participant trace: ${events.length} events, ${(t/1000).toFixed(0)}s, expect 5 locks`);

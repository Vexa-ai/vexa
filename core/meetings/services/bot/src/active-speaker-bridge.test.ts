/**
 * A direct handover must CLOSE the previous speaker, not only open the next.
 *
 * Zoom's adapter reports who is lit NOW (`name | null`), unlike Teams and Jitsi whose adapters emit
 * speaking start/end events directly. The binder needs both edges: it names a turn by max-overlap
 * against hint windows, so a speaker whose window never closes keeps winning against everyone who
 * follows. Emitting an end only when NOBODY is lit misses the case a real meeting actually produces
 * — A hands straight over to B, without a gap.
 *
 * Measured on the corpus entry zoom/2026-07-20-live-zoom (a live 5-person call): 142 hints, 13
 * speaker switches, and ZERO end hints — every switch was a direct handover. Two of the five
 * participants reached the transcript.
 *
 *   tsx src/active-speaker-bridge.test.ts
 */
import { makeActiveSpeakerBridge } from './capture-bridge.js';

let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}

const emitted: Array<{ name: string; tMs: number; isEnd: boolean }> = [];
let clock = 1000;
const bridge = makeActiveSpeakerBridge((name, tMs, isEnd) => emitted.push({ name, tMs, isEnd }), () => clock);

// Anna speaks, hands straight over to Boris, who hands straight over to Galina, and then the room
// falls silent. Not one of those transitions passes through "nobody lit".
bridge('Anna');   clock += 2000;
bridge('Anna');   clock += 2000;      // heartbeat re-assert on the same speaker
bridge('Boris');  clock += 2000;
bridge('Galina'); clock += 2000;
bridge(null);

const ends = emitted.filter((e) => e.isEnd);
const starts = emitted.filter((e) => !e.isEnd);

console.log('  emitted:');
for (const e of emitted) console.log(`    [${e.tMs}] ${e.isEnd ? 'END  ' : 'START'} ${e.name}`);

ok(starts.length === 4, 'every lit report opens or re-asserts a window (4 starts incl. the heartbeat)');
ok(ends.length === 3, 'every handover closes the speaker it replaced, and the silence closes the last');
ok(ends.map((e) => e.name).join(',') === 'Anna,Boris,Galina', 'each speaker is closed exactly once, in order');

// A heartbeat is not a handover: re-asserting the SAME name must not close it.
const annaEnds = emitted.filter((e) => e.isEnd && e.name === 'Anna');
ok(annaEnds.length === 1, 'a heartbeat re-assert does not close the speaker it re-asserts');

// The close must land BEFORE the next speaker opens, or the two windows overlap and the binder
// sees two candidates for the same instant.
const boris = emitted.findIndex((e) => !e.isEnd && e.name === 'Boris');
const annaEnd = emitted.findIndex((e) => e.isEnd && e.name === 'Anna');
ok(annaEnd >= 0 && annaEnd < boris, 'the outgoing speaker closes before the incoming one opens');

console.log(`\n✅ active-speaker-bridge: ${checks} checks passed — a handover closes one window and opens the next.`);

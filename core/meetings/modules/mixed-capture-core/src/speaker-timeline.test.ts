/** createSpeakerTimeline: time-aligned, by tile id, withheld for unnamed speakers.  npx tsx src/speaker-timeline.test.ts */
import { createSpeakerTimeline } from './speaker-timeline.js';
let failed = 0;
const check = (n: string, ok: boolean, d = '') => { console.log(`  ${ok ? '✅' : '❌'} ${n}${ok ? '' : '  — ' + d}`); if (!ok) failed++; };
const T = 1_700_000_000_000; const t = createSpeakerTimeline();
t.speak('id1', 'Maya', T + 200, false); t.speak('id2', 'Omar', T + 600, false); t.speak('id1', 'Maya', T + 900, true);
check('none, Maya, none (two), Omar by time', JSON.stringify([T, T + 256, T + 700, T + 1000].map((x) => t.nameAt(x))) === '[null,"Maya",null,"Omar"]',
  JSON.stringify([T, T + 256, T + 700, T + 1000].map((x) => t.nameAt(x))));
t.speak('a', 'Sam', T + 1100, false); t.speak('b', 'Sam', T + 1100, false);
check('two tiles with one name are two speakers', t.nameAt(T + 1200) === null);
t.speak('a', 'Sam', T + 1300, true); t.speak('b', 'Sam', T + 1300, true); t.speak('id2', 'Omar', T + 1300, true);
t.speak('id1', 'Maya', T + 1400, false); t.unnamed('start', T + 1450);
check('an unnamed speaker withholds the name', t.nameAt(T + 1500) === null);
t.unnamed('end', T + 1600);
check('…and after it ends the named one is back', t.nameAt(T + 1700) === 'Maya');
t.speak('id1', 'Maya', T + 1750, false);
check('a repeated start does not open a second span', (t.speak('id1', 'Maya', T + 1800, true), t.nameAt(T + 1900)) === null);
check('edges are counted', t.edges() === 13, String(t.edges()));
console.log(failed ? `\n${failed} FAILED` : '\nspeaker-timeline: all passed');
process.exit(failed ? 1 : 0);

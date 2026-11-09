/**
 * createTeamsPageCapture: the mixed lane's frames cross on channel 0, named by the one participant the
 * outline shows speaking (null for none or several); stop is final and releases both. Lane and speakers faked.
 *   npx tsx src/page-capture.test.ts
 */
import { createTeamsPageCapture } from './page-capture.js';
// the host injects the shared timeline; the test uses the same implementation
import { createSpeakerTimeline } from '../../mixed-capture-core/src/speaker-timeline.js';

let failed = 0;
const check = (n: string, ok: boolean, d = '') => { console.log(`  ${ok ? '✅' : '❌'} ${n}${ok ? '' : '  — ' + d}`); if (!ok) failed++; };
let laneOpts: any = null, speak: any = null, unresolved: any = null, laneStops = 0, destroyed = 0, self: any;
const got: any[] = [];
const cap = createTeamsPageCapture({
  selfName: 'Owner', streams: () => [], createTimeline: () => createSpeakerTimeline(),
  onAudio: (ch, name, pcm, ts) => got.push({ ch, name, n: pcm.length, ts }),
  createLane: (o: any) => { laneOpts = o; return { channels: () => 1, contextState: () => 'running', stop: () => { laneStops++; } }; },
  makeSpeakers: ((o: any) => { speak = o.onSpeaking; unresolved = o.onNameUnresolved; self = o.selfName; return { destroy: () => { destroyed++; } }; }) as any,
});
check('the lane is the Teams mixed lane (mainAudio pick)', laneOpts.teamsMainAudio === true && laneOpts.platform === 'teams');
check('the outline watcher excludes the local name', self === 'Owner');
const T0 = 1_700_000_000_000;
speak('Maya', 'id1', false, T0 + 200);
speak('Omar', 'id2', false, T0 + 600);
speak('Maya', 'id1', true, T0 + 900);
laneOpts.onAudio(new Float32Array(4096), T0);          // before anyone
laneOpts.onAudio(new Float32Array(4096), T0 + 256);    // Maya alone
laneOpts.onAudio(new Float32Array(4096), T0 + 700);    // both
laneOpts.onAudio(new Float32Array(4096), T0 + 1000);   // Omar alone (named at the frame's time, not at callback time)
check('named at each frame\'s capture time: none, Maya, none (two), Omar',
  JSON.stringify(got.map((g) => g.name)) === '[null,"Maya",null,"Omar"]', JSON.stringify(got.map((g) => g.name)));
speak('Sam', 'id3', false, T0 + 1100); speak('Sam', 'id4', false, T0 + 1100);
laneOpts.onAudio(new Float32Array(4096), T0 + 1200);
check('two tiles with one display name are two speakers (keyed by id): no name', got[4].name === null, JSON.stringify(got[4]));
speak('Sam', 'id3', true, T0 + 1300); speak('Sam', 'id4', true, T0 + 1300); speak('Omar', 'id2', true, T0 + 1300);
speak('Maya', 'id1', false, T0 + 1400);
unresolved({ edge: 'start', tMs: T0 + 1450 });
laneOpts.onAudio(new Float32Array(4096), T0 + 1500);
unresolved({ edge: 'end', tMs: T0 + 1600 });
laneOpts.onAudio(new Float32Array(4096), T0 + 1700);
check('an unnamed participant speaking withholds the name; after it ends the one named speaker is back',
  got[5].name === null && got[6].name === 'Maya', JSON.stringify(got.slice(5)));
check('everything crosses on channel 0 with the lane clock', got.every((g) => g.ch === 0) && got[1].ts === T0 + 256);
check('channels and context state come from the lane', cap.channels() === 1 && cap.contextState() === 'running');
cap.stop(); cap.stop();
laneOpts.onAudio(new Float32Array(10), 1);
check('stop is final and releases the lane and the watcher once', laneStops === 1 && destroyed === 1 && got.length === 7);
console.log(failed ? `\n${failed} FAILED` : '\nteams page-capture: all passed');
process.exit(failed ? 1 : 0);

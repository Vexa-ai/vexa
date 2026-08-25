import { TeamsCsrcChannelizer, type TeamsCsrcVirtualFrame } from './teams-csrc-channelizer.js';

let failed = 0;
const check = (name: string, condition: boolean, detail = ''): void => {
  console.log(`  ${condition ? '✅' : '❌'} ${name}${condition ? '' : ` — ${detail}`}`);
  if (!condition) failed++;
};

const pcm = (marker: number): Float32Array => new Float32Array([marker]);
const out: TeamsCsrcVirtualFrame[] = [];
const channelizer = new TeamsCsrcChannelizer({ lookbackMs: 600, flickerHoldMs: 0, onFrame: (frame) => out.push(frame) });

channelizer.feedAudio(pcm(9), 900);
channelizer.recordTransportEvent({ csrc: 201, active: true, tMs: 1000 });
channelizer.feedAudio(pcm(11), 1100);
channelizer.recordTransportEvent({ csrc: 414, active: true, tMs: 1050 });
channelizer.feedAudio(pcm(12), 1200);
channelizer.recordTransportEvent({ csrc: 201, active: false, tMs: 1300 });
channelizer.feedAudio(pcm(14), 1400);
channelizer.recordTransportEvent({ csrc: 201, active: true, tMs: 1500 });
channelizer.feedAudio(pcm(16), 1600);

const routed = out.map((f) => `${f.csrc}:${f.pcm[0]}:${f.backfilled ? 'b' : 'l'}`);
const expected = [
  '201:9:b',
  '201:11:l',
  '414:9:b', '414:11:b',
  '201:12:l', '414:12:l',
  '414:14:l',
  '201:14:b',
  '414:16:l', '201:16:l',
];
check('late activation backfills, overlap fans out, inactive stops, reactivation resumes',
  JSON.stringify(routed) === JSON.stringify(expected), JSON.stringify(routed));

const keys = out.map((f) => `${f.csrc}:${f.pcm[0]}`);
check('no (CSRC, frame) pair is emitted twice', new Set(keys).size === keys.length, keys.join(','));
check('overlap routes the original immutable PCM reference to both lanes',
  out.find((f) => f.csrc === 201 && f.pcm[0] === 12)?.pcm === out.find((f) => f.csrc === 414 && f.pcm[0] === 12)?.pcm);

const health = channelizer.health();
check('health exposes the bounded routing surface',
  health.tracks === 2 && health.transitions === 4 && health.inputFrames === 5
    && health.emittedFrames === expected.length && health.backfilledFrames === 4
    && health.maxConcurrency === 2 && health.provisional === 0
    && health.suppressedFlickers === 0 && health.promotedAfterHold === 0,
  JSON.stringify(health));

const smoothed: TeamsCsrcVirtualFrame[] = [];
const smoother = new TeamsCsrcChannelizer({
  lookbackMs: 600,
  flickerHoldMs: 1500,
  onFrame: (frame) => smoothed.push(frame),
});
smoother.recordTransportEvent({ csrc: 840, active: true, tMs: 0 });
smoother.feedAudio(pcm(0), 0);
smoother.feedAudio(pcm(5), 500);
smoother.recordTransportEvent({ csrc: 414, active: true, tMs: 1000 });
smoother.feedAudio(pcm(10), 1000);
smoother.feedAudio(pcm(15), 1500);
smoother.recordTransportEvent({ csrc: 414, active: false, tMs: 1842 });
smoother.feedAudio(pcm(19), 1900);
check('a short nested CSRC flicker receives no audio while the established owner continues',
  smoothed.every((frame) => frame.csrc === 840), JSON.stringify(smoothed));

smoother.recordTransportEvent({ csrc: 201, active: true, tMs: 2000 });
smoother.feedAudio(pcm(20), 2000);
smoother.feedAudio(pcm(25), 2500);
smoother.feedAudio(pcm(30), 3000);
smoother.feedAudio(pcm(35), 3501);
const promoted201 = smoothed.filter((frame) => frame.csrc === 201).map((frame) => frame.pcm[0]);
check('a surviving nested CSRC is promoted with its complete held onset',
  JSON.stringify(promoted201) === JSON.stringify([15, 19, 20, 25, 30, 35]), JSON.stringify(promoted201));
const smoothedHealth = smoother.health();
check('flicker decisions are observable',
  smoothedHealth.suppressedFlickers === 1 && smoothedHealth.promotedAfterHold === 1
    && smoothedHealth.provisional === 0,
  JSON.stringify(smoothedHealth));

// m26123 05:19 regression: an established source and its short nested flicker both ended at the
// exact same timestamp. Processing the established false edge first used to promote and backfill
// the flicker before its own false edge was seen.
const sameTimestampFrames: TeamsCsrcVirtualFrame[] = [];
const sameTimestamp = new TeamsCsrcChannelizer({
  lookbackMs: 600,
  flickerHoldMs: 1500,
  onFrame: (frame) => sameTimestampFrames.push(frame),
});
sameTimestamp.recordTransportEvent({ csrc: 201, active: true, tMs: 0 });
sameTimestamp.feedAudio(pcm(1), 100);
sameTimestamp.recordTransportEvent({ csrc: 840, active: true, tMs: 1000 });
sameTimestamp.feedAudio(pcm(11), 1100);
sameTimestamp.recordTransportEvent({ csrc: 201, active: false, tMs: 2108 });
sameTimestamp.recordTransportEvent({ csrc: 840, active: false, tMs: 2108 });
sameTimestamp.feedAudio(pcm(22), 2200);
check('same-timestamp owner/flicker false edges cannot backfill the flicker by callback order',
  !sameTimestampFrames.some((frame) => frame.csrc === 840), JSON.stringify(sameTimestampFrames));

const handoffFrames: TeamsCsrcVirtualFrame[] = [];
const handoff = new TeamsCsrcChannelizer({
  lookbackMs: 600,
  flickerHoldMs: 1500,
  onFrame: (frame) => handoffFrames.push(frame),
});
handoff.recordTransportEvent({ csrc: 201, active: true, tMs: 0 });
handoff.feedAudio(pcm(1), 100);
handoff.recordTransportEvent({ csrc: 840, active: true, tMs: 1000 });
handoff.feedAudio(pcm(11), 1100);
handoff.recordTransportEvent({ csrc: 201, active: false, tMs: 1200 });
handoff.feedAudio(pcm(13), 1300);
check('a surviving handoff promotes on the next PCM frame with its held onset',
  handoffFrames.some((frame) => frame.csrc === 840 && frame.tsMs === 1100 && frame.backfilled),
  JSON.stringify(handoffFrames));

if (failed > 0) process.exit(1);
console.log('\n✅ Teams CSRC channelizer routes the active set exactly once.');

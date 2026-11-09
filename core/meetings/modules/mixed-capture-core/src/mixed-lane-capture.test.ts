/**
 * createMixedLaneCapture — the mixed lane the bot (Teams/Jitsi) and the capture extension share. Fakes stand in
 * for the AudioContext, the streams, the mix tap and the Teams picker; what is pinned is the bot closure's
 * behaviour: presence from the stream objects, the Teams pick, the latched silence verdict, one summed tap,
 * metering, the capture clock passed through, late streams on rescan, restart, a final stop.
 *   npx tsx src/mixed-lane-capture.test.ts
 */
import { createMixedLaneCapture } from './mixed-lane-capture.js';

let failed = 0;
const check = (n: string, ok: boolean, d = '') => { console.log(`  ${ok ? '✅' : '❌'} ${n}${ok ? '' : '  — ' + d}`); if (!ok) failed++; };
const tick = () => new Promise((r) => setTimeout(r, 0));
const stream = (id: string, live = true): any => ({ id, getAudioTracks: () => [{ id, readyState: live ? 'live' : 'ended', muted: false }] });

const connected: string[] = []; let closed = false;
const ctx: any = {
  state: 'running', resume: () => Promise.resolve(), close() { closed = true; return Promise.resolve(); },
  createMediaStreamDestination: () => ({ stream: { id: 'dest' } }),
  createMediaStreamSource: (s: any) => ({ connect: () => connected.push(s.id) }),
};
let streams = [stream('mainAudio-1'), stream('dominant-2')];
const audio: any[] = []; const obs: any[] = []; const presence: number[] = []; let ready = 0;
let onPcm: any = null; let taps = 0; let tapStops = 0; let silent = false; let t = 1_700_000_000_000;
let tick2: any = null; let cleared = false; const selArgs: any[] = [];
const lane = createMixedLaneCapture({
  streams: () => streams, teamsMainAudio: true,
  onAudio: (pcm, ts) => audio.push({ n: pcm.length, ts }),
  observe: (_k, d) => obs.push(d), onPresence: (n) => presence.push(n), onReady: () => { ready++; },
  createAudioContext: () => ctx,
  createMixed: (async (_s: any, cb: any) => { taps++; onPcm = cb; return { stop: () => { tapStops++; } }; }) as any,
  select: ((ss: any[], a: any) => { selArgs.push(a); return { outcome: 'main-audio', streams: ss.filter((s) => s.id.startsWith('mainAudio')) }; }) as any,
  provedSilent: (() => silent) as any,
  setInterval: (fn) => { tick2 = fn; return 'H'; }, clearInterval: (h) => { cleared = h === 'H'; },
  now: () => t,
});
await tick(); await tick();
check('presence is counted from the stream objects summed so far (none before the first sum)', presence[0] === 0, JSON.stringify(presence));
check('on Teams only the picked mainAudio stream is summed', JSON.stringify(connected) === '["mainAudio-1"]', JSON.stringify(connected));
check('one tap over the sum, and ready is signalled', taps === 1 && ready === 1);
check('the topology is observed', obs.some((o) => o.type === 'mix-topology' && o.streams === 1));
onPcm(new Float32Array(1600).fill(0.1), t - 100);
onPcm(new Float32Array(1600), t);
check('frames are handed on with the capture clock (never receipt time)', audio.length === 2 && audio[0].ts === t - 100, JSON.stringify(audio));
check('energetic audio is metered, silence is not', lane.meter().frames === 2 && Math.abs(lane.meter().energeticMs - 100) < 1e-9, JSON.stringify(lane.meter()));
streams = [...streams, stream('mainAudio-3')];
t += 2000; tick2();
check('a late stream joins on rescan; presence now counts it', connected.includes('mainAudio-3') && presence[presence.length - 1] === 1 && lane.channels() === 2, JSON.stringify({ connected, presence }));
silent = true; t += 2000; tick2();
check('the picker gets the grace, the clock and the capture age it always did',
  selArgs[0].firstMissMs === null && selArgs[0].graceMs === 15000 && selArgs[0].mainAudioSilent === false
  && selArgs[0].mainAudioCapturedMs === 0 && selArgs[selArgs.length - 1].mainAudioCapturedMs > 0, JSON.stringify(selArgs.slice(-1)));
check('a picked mix proved silent is said once, as data', obs.filter((o) => o.type === 'main-audio-absent').length === 1 && lane.meter().mainAudioDead);
tick2();
check('…and latched (not said again)', obs.filter((o) => o.type === 'main-audio-absent').length === 1);
lane.restart(); tick2(); await tick(); await tick();
check('restart stops the tap and the next rescan makes a new one', tapStops === 1 && taps === 2 && ready === 2, JSON.stringify({ tapStops, taps, ready }));
lane.stop(); lane.stop();
const before = taps; tick2();
onPcm(new Float32Array(10).fill(0.5), t);
check('stop is final: interval cleared, tap stopped, context closed, nothing after', cleared && tapStops === 2 && closed && taps === before && audio.length === 2);

// Jitsi: no pick, every stream summed; fail open: a picker that returns every track sums them all
for (const [label, teams, pickAll] of [['Jitsi sums every stream (no pick)', false, false], ['Teams past grace fails open to every track', true, true]] as const) {
  const conn: string[] = []; const c2: any = { ...ctx, createMediaStreamSource: (s: any) => ({ connect: () => conn.push(s.id) }) };
  let firstMiss: any = 'unset';
  const l2 = createMixedLaneCapture({
    streams: () => [stream('a'), stream('b')], teamsMainAudio: teams, onAudio: () => {},
    createAudioContext: () => c2, createMixed: (async () => ({ stop() {} })) as any,
    select: ((ss: any[], a: any) => { firstMiss = a.firstMissMs; return { outcome: pickAll ? 'fallback-all' : 'main-audio', streams: ss }; }) as any,
    setInterval: () => 'X', clearInterval: () => {},
  });
  check(label, JSON.stringify(conn) === '["a","b"]' && (teams ? firstMiss === null : firstMiss === 'unset'), JSON.stringify({ conn, firstMiss }));
  l2.stop();
}
// restart while the tap is still being created: the stale tap is stopped, one tap stays
{
  let resolve: any = null; let made = 0; let stops = 0; let fn: any = null;
  const l3 = createMixedLaneCapture({
    streams: () => [stream('z')], onAudio: () => {}, createAudioContext: () => ctx,
    createMixed: (() => { made++; return new Promise((r) => { resolve = r; }); }) as any,
    setInterval: (f) => { fn = f; return 'Y'; }, clearInterval: () => {},
  });
  const first = resolve; l3.restart(); fn();               // a second create starts while the first is pending
  first({ stop: () => { stops++; } }); resolve({ stop: () => { stops += 10; } });
  await tick(); await tick();
  check('a restart during a pending create stops the stale tap and keeps one', made === 2 && stops === 1, JSON.stringify({ made, stops }));
  l3.stop();
}

console.log(failed ? `\n${failed} FAILED` : '\nmixed-lane-capture: all passed');
process.exit(failed ? 1 : 0);

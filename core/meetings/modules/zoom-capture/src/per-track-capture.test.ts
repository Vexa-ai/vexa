/**
 * createZoomPerTrackCapture — the per-track Zoom capture the bot and the extension share (card 66d7eaa642e5).
 * Fakes stand in for the AudioContext, the hooked streams and the DOM watcher; the behaviour pinned is the
 * one the bot's capture-bridge closure had: a channel per stream, silence gated, page-clock stamps, names
 * from the resolver (none while unbound), speaker edges into the resolver, late tracks on rescan, teardown.
 *   npx tsx src/per-track-capture.test.ts
 */
import { createZoomPerTrackCapture } from './per-track-capture.js';

let failed = 0;
const check = (name: string, ok: boolean, detail = '') => { console.log(`  ${ok ? '✅' : '❌'} ${name}${ok ? '' : '  — ' + detail}`); if (!ok) failed++; };

function stream(id: string, live = true): any {
  return { id, getAudioTracks: () => [{ readyState: live ? 'live' : 'ended', muted: false }] };
}
function fakeCtx() {
  const procs: any[] = []; const sources: any[] = [];
  const ctx: any = {
    destination: {}, closed: false, resumed: 0, state: 'running',
    resume() { this.resumed++; return Promise.resolve(); },
    close() { this.closed = true; return Promise.resolve(); },
    createMediaStreamSource(s: any) { const n = { s, connected: [] as any[], connect(x: any) { this.connected.push(x); }, disconnect() { this.connected = []; } }; sources.push(n); return n; },
    createScriptProcessor() { const p: any = { onaudioprocess: null, connect() {}, disconnect() { p.disconnected = true; } }; procs.push(p); return p; },
  };
  return { ctx, procs, sources };
}
const frame = (v: number) => ({ inputBuffer: { getChannelData: () => new Float32Array(4096).fill(v) } });

await (async () => {
  const { ctx, procs } = fakeCtx();
  let streams = [stream('a'), stream('b')];
  const audio: any[] = []; const obs: any[] = []; let ready = 0; let presence = -1;
  let tick: (() => void) | null = null; let cleared = false;
  const hot: any[] = []; const spoke: any[] = []; let speakerChange: any = null; let destroyed = false;
  let perf = 1000;
  let onBind: any = null;
  const cap = createZoomPerTrackCapture({
    streams: () => streams,
    selfName: 'Vexa',
    onAudio: (ch, name, samples, ts) => audio.push({ ch, name, n: samples.length, first: samples[0], ts }),
    observe: (k, d) => obs.push({ k, ...d }),
    onPresence: (n) => { presence = n; },
    onReady: () => { ready++; },
    createAudioContext: () => ctx,
    setInterval: (fn) => { tick = fn; return 'H'; },
    clearInterval: (h) => { cleared = h === 'H'; },
    nowPerf: () => perf,
    nowEpoch: () => 1_700_000_000_000 + perf,
    makeResolver: ((o: any) => (onBind = o.onBind, {
      mode: 'exclusive', setSelfName() {}, onSpeak: (n: any, t: any, e: any) => spoke.push({ n, e }),
      markHot: (ch: number, t: number, e: number) => hot.push({ ch, e }),
      resolve: (ch: number) => (ch === 1 ? 'Maya' : undefined),
      nameFor: () => undefined, labelFor: () => '', channels: () => [], bindings: () => [],
    })) as any,
    makeSpeakers: ((o: any) => { speakerChange = o.onSpeakerChange; return { destroy() { destroyed = true; }, getActiveSpeaker: () => null }; }) as any,
  });

  check('one channel per hooked stream, in order', cap.channels() === 2 && procs.length === 2);
  check('ready is signalled on every tapped track, as the bot did', ready === 2, String(ready));
  check('presence counts live tracks on the scan', presence === 2, String(presence));
  check('the topology is reported as data', obs.some((o) => o.type === 'pertrack-topology' && o.streams === 2), JSON.stringify(obs));
  check('the shared context is resumed', ctx.resumed === 1);

  procs[0].onaudioprocess(frame(0.001));
  check('a silent frame is gated', audio.length === 0 && hot.length === 0);
  perf = 2000;
  procs[0].onaudioprocess(frame(0.2));
  procs[1].onaudioprocess(frame(0.3));
  check('an unbound channel crosses with NO name', audio[0]?.ch === 0 && audio[0]?.name === null, JSON.stringify(audio[0]));
  check('a bound channel crosses with its resolved name', audio[1]?.ch === 1 && audio[1]?.name === 'Maya', JSON.stringify(audio[1]));
  check('frames carry the PCM', audio[0]?.n === 4096 && Math.abs(audio[0]?.first - 0.2) < 1e-6);
  const expectTs = 1_700_000_000_000 + 1000 - 1000 + 2000 - (4096 / 16000) * 1000;
  check('frames are stamped on the epoch-anchored page clock', Math.abs(audio[0]?.ts - expectTs) < 1e-6, `${audio[0]?.ts} vs ${expectTs}`);
  check('energy is marked on the resolver', hot.length === 2 && hot[1].ch === 1);
  {
    const buf = new Float32Array(4096).fill(0.4);
    procs[0].onaudioprocess({ inputBuffer: { getChannelData: () => buf } });
    const last = audio[audio.length - 1];
    buf.fill(0.9);
    check('the frame is a copy: reusing the input buffer does not change what crossed', Math.abs(last.first - 0.4) < 1e-6, String(last.first));
  }
  await Promise.resolve(); await Promise.resolve();
  check('the context state is reported as data', obs.some((o) => o.type === 'pertrack-context' && o.state === 'running') && cap.contextState() === 'running', JSON.stringify(obs));
  onBind(1, 'Maya', 3); onBind(1, 'Omar', 4);
  check('a bind and a flip are observed', obs.some((o) => o.type === 'pertrack-bind' && o.channel === 1 && o.name === 'Maya')
    && obs.some((o) => o.type === 'pertrack-flip' && o.previous === 'Maya' && o.name === 'Omar'), JSON.stringify(obs.filter((o) => o.type?.startsWith('pertrack-'))));

  speakerChange('Maya'); speakerChange(null);
  check('active-speaker edges reach the resolver (start, then end of the same name)',
    spoke.length === 2 && spoke[0].n === 'Maya' && spoke[0].e === false && spoke[1].n === 'Maya' && spoke[1].e === true, JSON.stringify(spoke));

  streams = [...streams, stream('c')];
  tick!();
  check('a late-joining track is picked up on the rescan', cap.channels() === 3 && procs.length === 3);

  cap.stop();
  check('stop clears the rescan, disconnects the taps, closes the context and the watcher',
    cleared && procs.every((p) => p.disconnected && p.onaudioprocess === null) && ctx.closed && destroyed);
  const before = procs.length;
  cap.stop();
  streams = [...streams, stream('d')];
  cap.rescan();
  check('a second stop is safe, and a rescan after stop taps nothing', procs.length === before && cap.channels() === 0);
})();

console.log(failed ? `\n${failed} FAILED` : '\nper-track-capture: all passed');
process.exit(failed ? 1 : 0);

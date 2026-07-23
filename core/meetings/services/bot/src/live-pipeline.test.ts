/**
 * L2 — createLivePipeline guard (#593 A4). The admitted→capture-start handoff had NO unit before
 * this: the composed pipeline was inline in index.ts (three bare awaits). This proves the
 * load-bearing invariant — a post-admission subsystem failure (page-side capture throw, recording
 * throw, or the engine/pyannote-model load rejecting) DEGRADES LOUDLY and NEVER rejects start(), so
 * the orchestrator's leave-on-pipeline-fail backstop never fires and the bot stays seated.
 *
 * RED on the pre-#593 inline pipeline (the first thrown await rejects start()); GREEN after.
 * Run: npx tsx src/live-pipeline.test.ts
 */
import { createLivePipeline, serr, type LiveStage } from './pipeline.js';
import type { Pipeline } from './ports.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

// A fake engine (the BotPipeline seen as a Pipeline) with a controllable start + start/stop counters.
const fakeEngine = (startImpl?: () => Promise<void>): Pipeline & { starts: number; stops: number } => {
  const e = {
    starts: 0, stops: 0,
    async start(): Promise<void> { e.starts++; if (startImpl) await startImpl(); },
    async stop(): Promise<void> { e.stops++; },
  };
  return e;
};

type Spy = { started: number; stopped: number };
const okThunk = (spy: Spy) => async (): Promise<() => Promise<void>> => { spy.started++; return async () => { spy.stopped++; }; };
const throwThunk = (e: unknown) => async (): Promise<() => Promise<void>> => { throw e; };
const tick = (ms = 0): Promise<void> => new Promise((r) => setTimeout(r, ms));

async function main(): Promise<void> {
  // 1) capture-start throws a fabricated {isTrusted:true} DOM-like Event → start() RESOLVES; engine
  //    still starts; the fault is reported. This is the exact #593 self-evict class.
  {
    const faults: LiveStage[] = [];
    const engine = fakeEngine();
    let resolved = false;
    const live = createLivePipeline({
      startCapture: throwThunk({ isTrusted: true, type: 'error' }),   // the misdirecting event shape
      engine,
      onFault: (s) => faults.push(s),
      retry: { attempts: 1, delayMs: 0 },
    });
    await live.start().then(() => { resolved = true; });
    check('capture throw: start() RESOLVED (no self-evict)', resolved);
    check('capture throw: engine STILL started after the capture throw', engine.starts === 1);
    check('capture throw: onFault(capture-start) fired', faults.includes('capture-start'));
  }

  // 2) engine-start throws (the pyannote model-load reject) → start() RESOLVES; fault reported.
  {
    const faults: LiveStage[] = [];
    const capSpy: Spy = { started: 0, stopped: 0 };
    const engine = fakeEngine(async () => { throw new Error('from_pretrained: config.json not found'); });
    let resolved = false;
    const live = createLivePipeline({
      startCapture: okThunk(capSpy),
      engine,
      onFault: (s) => faults.push(s),
      retry: { attempts: 1, delayMs: 0 },
    });
    await live.start().then(() => { resolved = true; });
    check('engine throw: start() RESOLVED (bot stays seated)', resolved);
    check('engine throw: onFault(engine-start) fired', faults.includes('engine-start'));
    check('engine throw: capture attached first', capSpy.started === 1);
  }

  // 3) recording-start throws → start() RESOLVES; fault reported; engine still starts.
  {
    const faults: LiveStage[] = [];
    const capSpy: Spy = { started: 0, stopped: 0 };
    const engine = fakeEngine();
    let resolved = false;
    const live = createLivePipeline({
      startCapture: okThunk(capSpy),
      startRecording: throwThunk(new Error('MediaRecorder boom')),
      engine,
      onFault: (s) => faults.push(s),
      retry: { attempts: 1, delayMs: 0 },
    });
    await live.start().then(() => { resolved = true; });
    check('recording throw: start() RESOLVED', resolved);
    check('recording throw: onFault(recording-start) fired', faults.includes('recording-start'));
    check('recording throw: engine STILL started', engine.starts === 1);
  }

  // 4) happy path → no faults; stop() tears down capture + recording + engine.
  {
    const faults: LiveStage[] = [];
    const stages: string[] = [];
    const capSpy: Spy = { started: 0, stopped: 0 };
    const recSpy: Spy = { started: 0, stopped: 0 };
    const engine = fakeEngine();
    const live = createLivePipeline({
      startCapture: okThunk(capSpy), startRecording: okThunk(recSpy), engine, onFault: (s) => faults.push(s),
      onStage: (stage, phase) => stages.push(`${stage}:${phase}`),
    });
    await live.start();
    check('happy: no faults', faults.length === 0, faults.join(','));
    check('happy: engine started', engine.starts === 1);
    await live.stop();
    check('happy: stop tore down capture', capSpy.stopped === 1);
    check('happy: stop tore down recording', recSpy.stopped === 1);
    check('happy: stop stopped engine', engine.stops === 1);
    check('happy: every teardown stage exposes started→finished telemetry',
      ['capture-stop', 'recording-finalize', 'engine-stop'].every((stage) =>
        stages.includes(`${stage}:started`) && stages.includes(`${stage}:finished`)),
      stages.join(','));
  }

  // 5) engine retry: fails once then succeeds → self-heals in the background without evicting.
  {
    let attempts = 0;
    const engine = fakeEngine(async () => { attempts++; if (attempts === 1) throw new Error('transient model load'); });
    const live = createLivePipeline({
      startCapture: okThunk({ started: 0, stopped: 0 }), engine, onFault: () => {}, retry: { attempts: 3, delayMs: 5 },
    });
    await live.start();
    check('retry: start() resolved despite first-attempt failure', true);
    await tick(40);   // let the background retry timer fire
    check('retry: engine eventually started (self-heal)', attempts >= 2, `attempts=${attempts}`);
    await live.stop();
  }

  // 6) stop() cancels a pending retry timer — no leaked timer, no post-stop start attempts.
  {
    let attempts = 0;
    const engine = fakeEngine(async () => { attempts++; throw new Error('always fails'); });
    const live = createLivePipeline({
      startCapture: okThunk({ started: 0, stopped: 0 }), engine, onFault: () => {}, retry: { attempts: 5, delayMs: 5 },
    });
    await live.start();
    const afterStart = attempts;
    await live.stop();
    await tick(40);
    check('stop: no further engine attempts after stop (timer cancelled)', attempts === afterStart, `after=${afterStart} now=${attempts}`);
  }

  // 7) serr: full-fidelity serialization — the A1 fix for the {isTrusted:true} fidelity loss.
  {
    const e = new Error('config.json not found');
    check('serr: Error → includes message', serr(e).includes('config.json not found'));
    check('serr: Error → includes a stack frame', /\bat\b/.test(serr(e)));
    check('serr: bare object → NOT flattened to [object …]', !serr({ isTrusted: true }).includes('[object'));
  }

  // 8) #934: teardown is bounded, names the timed-out stage, and is single-flight.
  {
    const never = (): Promise<void> => new Promise(() => { /* never resolves */ });
    const faults: { stage: LiveStage; detail: string }[] = [];
    const stages: string[] = [];
    const engine: Pipeline = { async start() { /* */ }, async stop() { await never(); } };
    const live = createLivePipeline({
      startCapture: async () => never,
      startRecording: async () => never,
      engine,
      onFault: (stage, e) => faults.push({ stage, detail: String(e) }),
      onStage: (stage, phase) => stages.push(`${stage}:${phase}`),
      teardownTimeoutMs: { 'capture-stop': 5, 'recording-finalize': 5, 'engine-stop': 5 },
    });
    await live.start();
    const startedAt = Date.now();
    const first = live.stop();
    const second = live.stop();
    check('#934 stop: repeated calls share the exact same in-flight promise', first === second);
    await first;
    check('#934 stop: all hung stages are bounded', Date.now() - startedAt < 150, `${Date.now() - startedAt}ms`);
    check('#934 stop: capture timeout is named before recording/engine teardown begins',
      faults[0]?.stage === 'capture-stop' &&
      stages.indexOf('capture-stop:started') < stages.indexOf('recording-finalize:started'),
      `faults=${JSON.stringify(faults)} stages=${stages.join(',')}`);
    check('#934 stop: recording-finalize and engine-stop faults are named independently',
      faults.some((f) => f.stage === 'recording-finalize' && f.detail.includes('timed out')) &&
      faults.some((f) => f.stage === 'engine-stop' && f.detail.includes('timed out')),
      JSON.stringify(faults));
  }

  // 9) #934: a retry already creating the engine cannot come alive after stop.
  {
    let starts = 0;
    let releaseRetry: () => void = () => {};
    const retryPending = new Promise<void>((resolve) => { releaseRetry = resolve; });
    const events: string[] = [];
    const engine: Pipeline = {
      async start() {
        starts++;
        events.push(`start-${starts}`);
        if (starts === 1) throw new Error('first start failed');
        await retryPending;
        events.push('retry-started');
      },
      async stop() { events.push('engine-stopped'); },
    };
    const live = createLivePipeline({
      startCapture: okThunk({ started: 0, stopped: 0 }),
      engine,
      onFault: () => {},
      retry: { attempts: 2, delayMs: 1 },
      teardownTimeoutMs: { 'engine-stop': 100 },
    });
    await live.start();
    for (let i = 0; i < 20 && starts < 2; i++) await tick(1);
    const stopP = live.stop();
    await tick(5);
    check('#934 retry-stop: teardown waits for the in-flight engine retry', !events.includes('engine-stopped'), events.join(','));
    releaseRetry();
    await stopP;
    check('#934 retry-stop: late engine success is stopped before teardown resolves',
      events.indexOf('retry-started') < events.indexOf('engine-stopped'),
      events.join(','));
  }

  console.log(failed === 0 ? '\n✅ live-pipeline: all passed' : `\n❌ live-pipeline: ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

void main();

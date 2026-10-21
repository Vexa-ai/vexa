/**
 * Capture liveness across the page→Node boundary (#1673) — the Teams mixed lane, end to end.
 *
 * Drives the REAL page-side bundle (dist/browser-utils.global.js — createMixedAudioCapture is the
 * production brick) and the REAL startCaptureBridge wiring against a fake Playwright Page, into a
 * REAL aloneness tap + silence source on a fake clock. The timeline is the field report: one Teams
 * server mix stays live and unmuted after the last human leaves, and it carries only silence.
 *
 * The capture brick gates silent frames before they cross (silence is not worth transcribing), so
 * no PCM ever reaches Node. Before liveness crossed separately, that read as "connected streams,
 * no frames" — a deaf capture — and the bot held the meeting until the capture-fault bound. With
 * the heartbeat, the bot hears that capture is alive and that the room is silent, and leaves after
 * max_time_left_alone.
 *
 * RED before the fix: no heartbeat crosses, nothing fires inside the window, the log says
 * capture-fault.
 * Run: npx tsx src/capture-liveness-wiring.test.ts
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { startCaptureBridge } from './capture-bridge.js';
import { createRemoteAudioActivityTap, createSilenceAlonenessSource } from './aloneness.js';
import type { Invocation } from './config.js';
import type { BotPipeline } from './pipeline.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string) => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

const BUNDLE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'dist', 'browser-utils.global.js');
if (!existsSync(BUNDLE)) {
  console.error(`❌ missing ${BUNDLE} — build the capture bundle first (pnpm --filter @vexa/bot build).`);
  process.exit(1);
}

// ── Page-context shims on the REAL globalThis (the fake page.evaluate runs in-process) ──
const g = globalThis as unknown as Record<string, unknown>;
const realSetInterval = globalThis.setInterval;
const realClearInterval = globalThis.clearInterval;
const intervals: Array<() => void> = [];
(g as any).setInterval = (cb: () => void) => { intervals.push(cb); return intervals.length; };
(g as any).clearInterval = () => { /* controlled clock */ };
g.document = { body: {}, querySelector: () => null, querySelectorAll: () => [] };
g.MutationObserver = class { observe() { /* unused */ } disconnect() { /* */ } };
g.window = g;

// Enough Web Audio for the mix (destination + sources) and the brick (a ScriptProcessor whose
// callback the test fires, plus the native-rate re-play context on a cloned track).
const processors: Array<{ onaudioprocess: ((e: unknown) => void) | null }> = [];
class FakeAudioContext {
  sampleRate: number;
  destination = {};
  constructor(opts?: { sampleRate?: number }) { this.sampleRate = opts?.sampleRate ?? 48000; }
  createMediaStreamDestination(): unknown {
    return { stream: { id: 'mix', getAudioTracks: () => [{ id: 'mix-track', clone: () => ({ stop() { /* */ } }) }] } };
  }
  createMediaStreamSource(): unknown { return { connect: () => { /* */ }, disconnect: () => { /* */ } }; }
  createScriptProcessor(): unknown {
    const proc = { onaudioprocess: null, connect: () => { /* */ }, disconnect: () => { /* */ } };
    processors.push(proc);
    return proc;
  }
  resume(): Promise<void> { return Promise.resolve(); }
  close(): Promise<void> { return Promise.resolve(); }
}
(g as any).AudioContext = FakeAudioContext;
(g as any).MediaStream = class { constructor(public tracks: unknown[] = []) {} getAudioTracks() { return this.tracks; } };
// The Teams server mix: live, unmuted — and after the last human left, silent.
g.__vexaCapturedRemoteAudioStreams = [
  { id: 'stream-main', getAudioTracks: () => [{ id: 'mainAudio-abc', readyState: 'live', muted: false }] },
];

new Function(readFileSync(BUNDLE, 'utf8'))();

const page = {
  async exposeFunction(name: string, fn: unknown): Promise<void> { g[name] = fn; },
  async evaluate(fn: (arg: never) => unknown, arg?: unknown): Promise<unknown> { return fn(arg as never); },
} as never;

let pcmFed = 0;
const pipeline: BotPipeline = {
  async start() { /* not driven */ },
  async stop() { /* not driven */ },
  feedAudio() { pcmFed++; },
  feedMixedAudio() { pcmFed++; },
  recordHint() { /* not driven */ },
};

// Aloneness on a fake clock: window 5s, bound 60s — a verdict near 5s is the silence rule, one near
// 60s would be the expired capture-fault hold.
let nowMs = 0;
const now = (): number => nowMs;
const activity = createRemoteAudioActivityTap({ now });
const pollers: Array<() => void> = [];
const logs: string[] = [];
const source = createSilenceAlonenessSource({
  activity,
  windowMs: 5_000,
  captureFaultMaxMs: 60_000,
  now,
  pollMs: 1_000,
  setInterval: (cb) => { pollers.push(cb); return pollers.length; },
  clearInterval: () => { pollers.length = 0; },
  log: (m) => logs.push(m),
});

const inv = { platform: 'teams', botName: 'Vexa Bot', connectionId: 'test' } as unknown as Invocation;
const stop = await startCaptureBridge(page, inv, pipeline, undefined, undefined, activity);
for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));   // the brick's async create + ready

check('the mixed lane built the capture brick over the server mix', processors.length === 1,
  `processors=${processors.length}`);
check('capture reported ready', activity.snapshot().available === true);
for (const cb of [...intervals]) cb();   // one rescan: presence is reported over the now-connected mix
check('the page reported the server mix as present', activity.snapshot().streamsConnected === 1,
  JSON.stringify(activity.snapshot()));

let firedAtMs: number | null = null;
source.onAlone(() => { firedAtMs = nowMs; });

// 20 s of an empty room: the brick's callback runs every 256 ms of audio (4096 samples @ 16 kHz)
// over pure silence; the page rescans (and re-reports presence) every 2 s; aloneness polls every 1 s.
const silent = new Float32Array(4096);
for (let t = 256; t <= 20_000 && firedAtMs === null; t += 256) {
  nowMs = t;
  processors[0].onaudioprocess?.({ inputBuffer: { getChannelData: () => silent } });
  if (t % 2_048 === 0) for (const cb of [...intervals]) cb();
  if (t % 1_024 === 0) for (const cb of [...pollers]) cb();
}

check('no silent PCM crossed to the pipeline (the gate still keeps silence away from STT)', pcmFed === 0,
  `fed=${pcmFed}`);
check('capture liveness crossed page→Node apart from the gate',
  (activity.snapshot().samplesProcessed ?? 0) > 0, JSON.stringify(activity.snapshot()));
check('silent-but-alive Teams mix + nobody speaking → left_alone at max_time_left_alone (not the fault bound)',
  firedAtMs !== null && firedAtMs >= 5_000 && firedAtMs < 10_000, `firedAtMs=${firedAtMs} logs=${JSON.stringify(logs)}`);
check('…and the empty room was never misread as a deaf capture', !logs.some((m) => m.includes('capture-fault')),
  JSON.stringify(logs));

await stop();
(g as any).setInterval = realSetInterval;
(g as any).clearInterval = realClearInterval;

if (failed) { console.error(`\n❌ capture-liveness-wiring: ${failed} checks FAILED.`); process.exit(1); }
console.log('\n✅ capture-liveness-wiring: the real bundle + real bridge carry capture liveness apart from the silence gate, so a silent Teams room leaves on the silence rule instead of holding as a deaf capture.');

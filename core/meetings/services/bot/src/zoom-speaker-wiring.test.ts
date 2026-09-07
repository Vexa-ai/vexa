/**
 * Zoom speaker wiring — the page→Node boundary test (#538 A1).
 *
 * Drives the REAL page-side capture bundle (dist/browser-utils.global.js — the
 * exact file the bot injects via addInitScript) and the REAL startCaptureBridge
 * wiring against a fake Playwright Page whose exposeFunction/evaluate run
 * in-process. Scripted Zoom active-speaker DOM transitions must cross the
 * boundary — name, epoch-ms timestamp, order, and turn-close (isEnd) intact.
 *
 * ── WHICH SEAM, and why this test moved ──────────────────────────────────────
 * It used to assert the transitions arrive at `BotPipeline.recordHint`, "which
 * the mixed lane labels 'dom-active'". That is no longer true for Zoom: Zoom now
 * captures PER-TRACK and rides the per-channel engine, whose pipeline implements
 * `recordHint() { /* not the gmeet lane *\/ }` — a no-op (pipeline.ts). The test
 * kept passing only because it injects its own spy pipeline, so it was guarding a
 * seam production discards. Same defect the author found and fixed in
 * csrc-wiring.test.ts (commit 389dfa20); this is its sibling.
 *
 * The two seams Zoom ACTUALLY uses, and what is asserted here now:
 *   1. the page-side RESOLVER (`__vexaTrackNamer.onSpeak` — @vexa/zoom-capture's
 *      createTrackNameResolver): the naming consumer, page-side, before the audio
 *      crosses at all;
 *   2. the TELEMETRY hint tee (`captureHint`): what a tape carries, and the hop
 *      the clock guard re-stamps.
 * The `recordHint` arrival is still observed — the bridge does call it — but it is
 * now asserted as what it is: a hop the Zoom pipeline drops on the floor.
 *
 * Also pinned here, because they are per-track wiring facts nothing else covers:
 *   3. stream PRESENCE reaches the aloneness tap (#1195 — a lane that never
 *      reports presence silently removes its platform from the deaf-capture guard);
 *   4. the lane's typed OBSERVATIONS cross as data, not as log lines (P18).
 *
 * RED at any base where the bundle lacks @vexa/zoom-capture or the zoom branch
 * of the bridge doesn't start the watcher: zero arrivals.
 * Run: npx tsx src/zoom-speaker-wiring.test.ts
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { startCaptureBridge } from './capture-bridge.js';
import type { Invocation } from './config.js';
import type { BotPipeline } from './pipeline.js';
import type { RemoteAudioActivityTap } from './aloneness.js';
import type { TelemetrySink } from './telemetry.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string) => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

// ── The real bundle (built by build-browser-utils.mjs — turbo test depends on build) ──
const BUNDLE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'dist', 'browser-utils.global.js');
if (!existsSync(BUNDLE)) {
  console.error(`❌ missing ${BUNDLE} — build the capture bundle first (pnpm --filter @vexa/bot build).`);
  process.exit(1);
}

// ── Minimal Zoom DOM shim: exactly what createZoomSpeakers queries ──
// (.speaker-active-container__video-frame → .video-avatar__avatar-footer → span)
class El {
  constructor(public classes: string[], public kids: El[] = [], public text = '') {}
  get textContent(): string { return this.text + this.kids.map((k) => k.textContent).join(''); }
  get innerText(): string { return this.textContent; }
  querySelector(sel: string): El | null {
    for (const k of this.all()) if (k.matches(sel)) return k;
    return null;
  }
  querySelectorAll(sel: string): El[] { return this.all().filter((k) => k.matches(sel)); }
  matches(sel: string): boolean {
    if (sel === 'span') return this.classes.includes('__span');
    return sel.split(',').some((s) => s.trim().startsWith('.') && this.classes.includes(s.trim().slice(1)));
  }
  private all(): El[] { const out: El[] = []; const w = (e: El) => { for (const k of e.kids) { out.push(k); w(k); } }; w(this); return out; }
}
const tile = (name: string) =>
  new El(['speaker-active-container__video-frame'], [
    new El(['video-avatar__avatar-footer'], [new El(['__span'], [], name)]),
  ]);
let root = new El(['body']);
const setSpeaker = (name: string | null): void => { root = new El(['body'], name ? [tile(name)] : []); };

// ── Page-context shims on the REAL globalThis (the fake page.evaluate runs in-process) ──
const g = globalThis as unknown as Record<string, unknown>;
g.document = { querySelector: (s: string) => root.querySelector(s), querySelectorAll: (s: string) => root.querySelectorAll(s) };
const intervals: Array<() => void> = [];
const realSetInterval = globalThis.setInterval;
const realClearInterval = globalThis.clearInterval;
(g as any).setInterval = (cb: () => void) => { intervals.push(cb); return intervals.length; };
(g as any).clearInterval = () => { /* controlled clock */ };
g.window = g;   // the bundle hangs VexaBrowserUtils on window too

// Two remote participant streams, both live — the per-track lane's input, and the presence oracle's.
const remoteStream = (id: string) => ({ id, getAudioTracks: () => [{ id: `${id}-a`, readyState: 'live', muted: false }] });
g.__vexaCapturedRemoteAudioStreams = [remoteStream('stream-1'), remoteStream('stream-2')];
// The per-track tap's AudioContext: enough surface for one source + one processor per track.
(g as any).AudioContext = class {
  destination = {};
  resume(): void { /* no-op */ }
  close(): void { /* no-op */ }
  createMediaStreamSource(): unknown { return { connect: () => { /* */ }, disconnect: () => { /* */ } }; }
  createScriptProcessor(): unknown {
    return { onaudioprocess: null, connect: () => { /* */ }, disconnect: () => { /* */ } };
  }
};

// Load the REAL bundle — defines globalThis.VexaBrowserUtils.
new Function(readFileSync(BUNDLE, 'utf8'))();
const utils = g.VexaBrowserUtils as Record<string, unknown> | undefined;
check('bundle: window.VexaBrowserUtils.createZoomSpeakers is exported (RED at base — brick not bundled)',
  typeof utils?.createZoomSpeakers === 'function', `keys: ${Object.keys(utils ?? {}).join(',')}`);
check('bundle: window.VexaBrowserUtils.createTrackNameResolver is exported (the namer is a MODULE, not a page literal)',
  typeof utils?.createTrackNameResolver === 'function', `keys: ${Object.keys(utils ?? {}).join(',')}`);

// ── Fake Playwright Page: exposeFunction hangs the Node fn on the page global (same
//    name Playwright binds); evaluate runs the callback in-process over those shims. ──
const page = {
  async exposeFunction(name: string, fn: unknown): Promise<void> { g[name] = fn; },
  async evaluate(fn: (arg: never) => unknown, arg?: unknown): Promise<unknown> { return fn(arg as never); },
} as never;

// ── Node side: every seam the transitions could reach ──
// recordHint: the bridge calls it, and the REAL Zoom pipeline throws it away — kept so the hop is
// observable, no longer asserted as the seam that names anything.
const hints: Array<{ name: string; tMs: number; isEnd: boolean }> = [];
const pipeline: BotPipeline = {
  async start() { /* not driven */ },
  async stop() { /* not driven */ },
  feedAudio() { /* not driven */ },
  feedMixedAudio() { /* not driven */ },
  recordHint: (name, tMs, isEnd) => hints.push({ name, tMs, isEnd: !!isEnd }),
};
const captured: Array<{ name: string; t: number; isEnd?: boolean }> = [];
const observations: Array<{ source: string; observation: Record<string, unknown> }> = [];
const telemetry = {
  captureHint: (h: { name: string; t: number; isEnd?: boolean }) => captured.push(h),
  captureObservation: (o: { source: string; observation: Record<string, unknown> }) => observations.push(o),
} as unknown as TelemetrySink;
const presence: number[] = [];
const activity = {
  ready: () => { /* not driven */ },
  unavailable: () => { /* not driven */ },
  observeStreamPresence: (n: number) => presence.push(n),
} as unknown as RemoteAudioActivityTap;
const inv = { platform: 'zoom', botName: 'Vexa Bot', connectionId: 'test' } as unknown as Invocation;

const t0 = Date.now();
const stop = await startCaptureBridge(page, inv, pipeline, telemetry, undefined, activity);

// The resolver the bridge built from the bundle — wrap it to observe what reaches it, delegating so
// the real algorithm still runs.
const resolver = g.__vexaTrackNamer as { onSpeak: (n: string | null, t: number, e: boolean) => void } | undefined;
const spoken: Array<{ name: string | null; tMs: number; isEnd: boolean }> = [];
if (resolver) {
  const inner = resolver.onSpeak.bind(resolver);
  resolver.onSpeak = (name, tMs, isEnd) => { spoken.push({ name, tMs, isEnd }); inner(name, tMs, isEnd); };
}
check('the per-track lane built the resolver from the bundle (not a literal in the bridge)', !!resolver);

const tick = (n: number) => { for (const cb of [...intervals]) for (let i = 0; i < n; i++) cb(); };

// ── N scripted transitions across the boundary (CONFIRM_POLLS=2 debounce) ──
setSpeaker('Alice');  tick(2);
setSpeaker('Bob');    tick(2);
setSpeaker('Carol');  tick(2);
setSpeaker(null);     tick(2);   // nobody lit → the open turn closes (isEnd)

// 1. THE NAMING SEAM ON THIS LANE — the page-side resolver, before any audio crosses.
const spokenStarts = spoken.filter((s) => !s.isEnd).map((s) => s.name);
check('seam 1 (resolver): all 3 scripted transitions reached the page-side namer, in order',
  JSON.stringify(spokenStarts) === JSON.stringify(['Alice', 'Bob', 'Carol']), JSON.stringify(spoken));
check('seam 1 (resolver): nobody-lit closes the last turn (isEnd for Carol)',
  spoken.some((s) => s.isEnd && s.name === 'Carol'), JSON.stringify(spoken));

// 2. THE TAPE — the hint tee, which is what a replay is reconstructed from.
const capturedStarts = captured.filter((h) => !h.isEnd).map((h) => h.name);
check('seam 2 (telemetry): the same transitions crossed to the tape, in order',
  JSON.stringify(capturedStarts) === JSON.stringify(['Alice', 'Bob', 'Carol']), JSON.stringify(captured));
check('seam 2 (telemetry): timestamps are epoch ms (the Node clock), non-decreasing',
  captured.every((h) => h.t >= t0 && h.t <= Date.now()) &&
  captured.every((h, i) => i === 0 || h.t >= captured[i - 1].t));

// 3. recordHint — observed, and explicitly NOT the naming seam here.
check('recordHint is a hop, not the seam: it receives the hints, and the Zoom pipeline discards them',
  hints.length === captured.length, `${hints.length} vs ${captured.length}`);

// 4. PRESENCE (#1195) — the lane must not silently opt Zoom out of the deaf-capture guard.
check('presence reaches the aloneness tap — 2 live remote streams (RED before the per-track fix: never called)',
  presence.length > 0 && presence[presence.length - 1] === 2, JSON.stringify(presence));

// 5. OBSERVATIONS (P18) — the lane's shape crosses as DATA, not only as a log line.
check('the per-track topology crossed as DATA (2 tracks captured)',
  observations.some((o) => o.observation.type === 'pertrack-topology' && o.observation.streams === 2),
  JSON.stringify(observations.map((o) => o.observation.type)));

// A single-poll flicker must NOT cross the boundary (the debounce holds at wiring altitude).
setSpeaker('Dave'); tick(2);
const before = spoken.length;
setSpeaker('Eve');  tick(1);    // one flicker poll
setSpeaker('Dave'); tick(1);    // back before confirm
check('boundary: a single-poll flicker (Eve) never crosses', !spoken.slice(before).some((s) => s.name === 'Eve'),
  JSON.stringify(spoken.slice(before)));

await stop();
(g as any).setInterval = realSetInterval;
(g as any).clearInterval = realClearInterval;

if (failed) { console.error(`\n❌ zoom-speaker-wiring: ${failed} checks FAILED.`); process.exit(1); }
console.log('\n✅ zoom-speaker-wiring: real bundle + real bridge carry Zoom active-speaker transitions page→Node to the seams the per-track lane actually uses (resolver, tape, presence, typed observations).');

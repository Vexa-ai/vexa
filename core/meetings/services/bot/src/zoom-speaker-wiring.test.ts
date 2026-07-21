/**
 * Zoom speaker wiring — the page→Node boundary test (#538 A1).
 *
 * Drives the REAL page-side capture bundle (dist/browser-utils.global.js — the
 * exact file the bot injects via addInitScript) and the REAL startCaptureBridge
 * wiring against a fake Playwright Page whose exposeFunction/evaluate run
 * in-process. Scripted Zoom active-speaker DOM transitions must cross the
 * boundary as speaker hints — name, epoch-ms timestamp, order, and turn-close
 * (isEnd) intact — arriving at the pipeline's recordHint seam (which the mixed
 * lane labels 'dom-active', Zoom's true kind — pipeline.ts).
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

// Load the REAL bundle — defines globalThis.VexaBrowserUtils.
new Function(readFileSync(BUNDLE, 'utf8'))();
const utils = g.VexaBrowserUtils as Record<string, unknown> | undefined;
check('bundle: window.VexaBrowserUtils.createZoomSpeakers is exported (RED at base — brick not bundled)',
  typeof utils?.createZoomSpeakers === 'function', `keys: ${Object.keys(utils ?? {}).join(',')}`);

// ── Fake Playwright Page: exposeFunction hangs the Node fn on the page global (same
//    name Playwright binds); evaluate runs the callback in-process over those shims. ──
const page = {
  async exposeFunction(name: string, fn: unknown): Promise<void> { g[name] = fn; },
  async evaluate(fn: (arg: never) => unknown, arg?: unknown): Promise<unknown> { return fn(arg as never); },
  on() { /* no-op: framenavigated re-arm not driven by this test */ },
  mainFrame() { return {}; },
} as never;

// ── Node side: the pipeline seam the hints must reach ──
const hints: Array<{ name: string; tMs: number; isEnd: boolean }> = [];
const pipeline: BotPipeline = {
  async start() { /* not driven */ },
  async stop() { /* not driven */ },
  feedAudio() { /* not driven */ },
  feedMixedAudio() { /* not driven */ },
  recordHint: (name, tMs, isEnd) => hints.push({ name, tMs, isEnd: !!isEnd }),
};
const inv = { platform: 'zoom', botName: 'Vexa Bot', connectionId: 'test' } as unknown as Invocation;

const t0 = Date.now();
const stop = await startCaptureBridge(page, inv, pipeline);
const tick = (n: number) => { for (const cb of [...intervals]) for (let i = 0; i < n; i++) cb(); };

// ── N scripted transitions across the boundary (CONFIRM_POLLS=2 debounce) ──
setSpeaker('Alice');  tick(2);
setSpeaker('Bob');    tick(2);
setSpeaker('Carol');  tick(2);
setSpeaker(null);     tick(2);   // nobody lit → the open turn closes (isEnd)

const starts = hints.filter((h) => !h.isEnd).map((h) => h.name);
check('boundary: all 3 scripted transitions arrived node-side, in order (RED at base — zero arrivals)',
  JSON.stringify(starts) === JSON.stringify(['Alice', 'Bob', 'Carol']), JSON.stringify(hints));
check('boundary: nobody-lit closes the last turn (isEnd for Carol)',
  hints.some((h) => h.isEnd && h.name === 'Carol'), JSON.stringify(hints));
check('boundary: timestamps are epoch ms (the Node clock), non-decreasing',
  hints.every((h) => h.tMs >= t0 && h.tMs <= Date.now()) &&
  hints.every((h, i) => i === 0 || h.tMs >= hints[i - 1].tMs));
check('boundary: the seam reached is BotPipeline.recordHint — the mixed lane labels it \'dom-active\' (pipeline.ts)',
  hints.length > 0);

// A single-poll flicker must NOT cross the boundary (the debounce holds at wiring altitude).
setSpeaker('Dave'); tick(2);
const before = hints.length;
setSpeaker('Eve');  tick(1);    // one flicker poll
setSpeaker('Dave'); tick(1);    // back before confirm
check('boundary: a single-poll flicker (Eve) never crosses', !hints.slice(before).some((h) => h.name === 'Eve'),
  JSON.stringify(hints.slice(before)));

await stop();
(g as any).setInterval = realSetInterval;
(g as any).clearInterval = realClearInterval;

if (failed) { console.error(`\n❌ zoom-speaker-wiring: ${failed} checks FAILED.`); process.exit(1); }
console.log('\n✅ zoom-speaker-wiring: real bundle + real bridge carry Zoom active-speaker transitions page→Node (order, epoch-ms timestamps, isEnd, flicker debounce).');

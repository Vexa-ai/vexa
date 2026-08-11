/**
 * Teams caption wiring — the page→Node boundary test for the live-CC lane.
 *
 * Drives the REAL page-side capture bundle (dist/browser-utils.global.js — the exact file the bot
 * injects via addInitScript) and the REAL startCaptureBridge wiring against a fake Playwright Page
 * whose exposeFunction/evaluate run in-process, over a Teams-shaped caption DOM built from the
 * 0.10 selectors. Scripted caption entries must cross the boundary as caption records — name,
 * text, epoch-ms timestamp, stability — and reach the telemetry sink.
 *
 * The load-bearing NEGATIVE check is the last one: a caption must NEVER reach
 * pipeline.recordHint. This iteration buys an observation stream, not a behaviour change, and a
 * caption that quietly became a naming hint would rewrite speaker attribution while claiming to
 * merely watch it.
 *
 * It also covers the join-time ENABLE flow (captions are switched ON at join): the guest menu path
 * from 0.10, and the failure path, which must end in a typed `captions-enable-failed` observation
 * and NEVER in a throw — a meeting whose tenant blocks captions still joins, still transcribes, and
 * still attributes speakers from the voice-level outline.
 *
 * RED at any base where the bundle lacks createTeamsCaptions or the teams branch of the bridge
 * doesn't start the watcher: zero arrivals.
 * Run: npx tsx src/teams-caption-wiring.test.ts
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { startCaptureBridge, makeTeamsCaptionSink, runTeamsCaptionEnable, type TeamsCaptionRecord, type CaptionCapableSink } from './capture-bridge.js';
import type { Invocation } from './config.js';
import type { BotPipeline } from './pipeline.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string) => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

// ── 1) The Node-side sink, driven directly (no page): clock guard + record shape ──
{
  const stored: TeamsCaptionRecord[] = [];
  const warnings: string[] = [];
  const sink = makeTeamsCaptionSink(
    { captureFrame() { /* unused */ }, captureCaption: (c) => stored.push(c) } as CaptionCapableSink,
    () => { /* quiet */ },
    (m) => warnings.push(m),
  );
  const t = Date.now();
  sink.sink('Priya Nair', 'we should ship the CC lane', t, true);
  check('sink: the record is typed caption/teams/mixed with name + text intact',
    stored.length === 1 && stored[0].type === 'caption' && stored[0].platform === 'teams'
    && stored[0].lane === 'mixed' && stored[0].name === 'Priya Nair'
    && stored[0].text === 'we should ship the CC lane' && stored[0].stable === true, JSON.stringify(stored));
  // A page emitting performance.now() instead of epoch ms would store captions against a clock
  // nothing else shares — re-stamped, and said out loud.
  sink.sink('Sven Olsen', 'relative clock', 42, true);
  check('sink: a non-epoch timestamp is re-stamped AND warned, never stored silently',
    warnings.some((w) => w.includes('caption-clock-skew')) && stored[1].t >= t, warnings.join(' | '));
  check('sink: counts what crossed and what was stored', sink.count() === 2 && sink.stored() === 2);

  // A sink with no captureCaption (today's recorder) must still be safe: log-only, never a throw.
  const noStore = makeTeamsCaptionSink({ captureFrame() { /* unused */ } }, () => { /* quiet */ });
  noStore.sink('Ana Ruiz', 'no tape today', Date.now(), false);
  check('sink: a recorder without captureCaption degrades to log-only (0 stored, no throw)',
    noStore.count() === 1 && noStore.stored() === 0);
}

// ── The real bundle (built by build-browser-utils.mjs — turbo test depends on build) ──
const BUNDLE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'dist', 'browser-utils.global.js');
if (!existsSync(BUNDLE)) {
  console.error(`❌ missing ${BUNDLE} — build the capture bundle first (pnpm --filter @vexa/bot build).`);
  process.exit(1);
}

// ── Minimal Teams caption DOM shim: exactly the atoms createTeamsCaptions queries ──
// (wrapper [data-tid="closed-caption-renderer-wrapper"] > rows of
//  [data-tid="author"] + [data-tid="closed-caption-text"] — the 0.10 pair, guest-view shape.)
class El {
  constructor(public attrs: Record<string, string> = {}, public kids: El[] = [], public text = '') {}
  get textContent(): string { return this.text + this.kids.map((k) => k.textContent).join(''); }
  getAttribute(n: string): string | null { return n in this.attrs ? this.attrs[n] : null; }
  querySelector(sel: string): El | null { for (const k of this.all()) if (k.matches(sel)) return k; return null; }
  querySelectorAll(sel: string): El[] { return this.all().filter((k) => k.matches(sel)); }
  /** Supports only `[attr="v"]` / `[attr*="v"]` (comma lists included) — every other selector
   *  shape the Teams bricks probe for simply matches nothing, which is the honest answer for a
   *  page that has captions and no participant tiles. */
  matches(sel: string): boolean {
    return sel.split(',').some((one) => {
      const m = one.trim().match(/^\[([a-zA-Z0-9_-]+)(?:(\*?=)"([^"]*)")?\]$/);
      if (!m) return false;
      const v = this.getAttribute(m[1]);
      if (v == null) return false;
      if (!m[2]) return true;
      return m[2] === '*=' ? v.includes(m[3]) : v === m[3];
    });
  }
  private all(): El[] { const out: El[] = []; const w = (e: El) => { for (const k of e.kids) { out.push(k); w(k); } }; w(this); return out; }
}
const row = (author: string, text: string) => new El({}, [
  new El({ 'data-tid': 'author' }, [], author),
  new El({ 'data-tid': 'closed-caption-text' }, [], text),
]);
const list = new El({ 'data-tid': 'closed-caption-v2-virtual-list-content' }, [row('Priya Nair', 'first turn here')]);
const root = new El({ 'data-tid': 'body' }, [ new El({ 'data-tid': 'closed-caption-renderer-wrapper' }, [list]) ]);

// ── Page-context shims on the REAL globalThis (the fake page.evaluate runs in-process) ──
const g = globalThis as unknown as Record<string, unknown>;
g.document = {
  body: root,
  querySelector: (s: string) => (root.matches(s) ? root : root.querySelector(s)),
  querySelectorAll: (s: string) => (root.matches(s) ? [root, ...root.querySelectorAll(s)] : root.querySelectorAll(s)),
};
g.MutationObserver = class { observe() { /* the poll drives this test */ } disconnect() { /* */ } };
const intervals: Array<() => void> = [];
const realSetInterval = globalThis.setInterval;
const realClearInterval = globalThis.clearInterval;
(g as any).setInterval = (cb: () => void) => { intervals.push(cb); return intervals.length; };
(g as any).clearInterval = () => { /* controlled clock */ };
g.window = g;   // the bundle hangs VexaBrowserUtils on window too

// Load the REAL bundle — defines globalThis.VexaBrowserUtils.
new Function(readFileSync(BUNDLE, 'utf8'))();
const utils = g.VexaBrowserUtils as Record<string, unknown> | undefined;
check('bundle: window.VexaBrowserUtils.createTeamsCaptions is exported (RED at base — brick not bundled)',
  typeof utils?.createTeamsCaptions === 'function', `keys: ${Object.keys(utils ?? {}).join(',')}`);

// ── Fake Playwright Page + the Node seams ──
const page = {
  async exposeFunction(name: string, fn: unknown): Promise<void> { g[name] = fn; },
  async evaluate(fn: (arg: never) => unknown, arg?: unknown): Promise<unknown> { return fn(arg as never); },
} as never;

const hints: Array<{ name: string; tMs: number; isEnd: boolean }> = [];
const captionNames: Array<{ name: string; tMs: number }> = [];
const pipeline: BotPipeline = {
  async start() { /* not driven */ },
  async stop() { /* not driven */ },
  feedAudio() { /* not driven */ },
  feedMixedAudio() { /* not driven */ },
  recordHint: (name, tMs, isEnd) => hints.push({ name, tMs, isEnd: !!isEnd }),
  recordCaptionName: (name, tMs) => captionNames.push({ name, tMs }),
};
const captured: TeamsCaptionRecord[] = [];
const telemetry: CaptionCapableSink = { captureFrame() { /* unused */ }, captureCaption: (c) => captured.push(c) };
const inv = { platform: 'teams', botName: 'Vexa Bot', connectionId: 'test' } as unknown as Invocation;

const t0 = Date.now();
const stop = await startCaptureBridge(page, inv, pipeline, telemetry);
const tick = (n: number) => { for (const cb of [...intervals]) for (let i = 0; i < n; i++) cb(); };

tick(2);
check('boundary: an entry still being refined has NOT crossed yet (no keystroke-level emission)',
  captured.length === 0, JSON.stringify(captured));

// A new entry at the tail supersedes the previous one — it can never change again, so it is final.
list.kids.push(row('Sven Olsen', 'second turn'));
tick(2);
check('boundary: the superseded entry crossed page→Node with name + text intact',
  captured.length === 1 && captured[0].name === 'Priya Nair' && captured[0].text === 'first turn here'
  && captured[0].stable === true, JSON.stringify(captured));
check('boundary: the caption timestamp is epoch ms (the same clock the audio frames carry)',
  captured[0] !== undefined && captured[0].t >= t0 && captured[0].t <= Date.now());

// CC is the SECOND source, never a replacement: the voice-level-outline watcher must be running on
// the same Teams branch (checked BEFORE teardown, which nulls both), so a caption outage — or a
// caption lane that never starts — cannot take speaker attribution down with it.
check('parallel sources: the DOM voice-level watcher runs alongside the caption reader',
  !!g.__vexaTeamsSpeakers && !!g.__vexaTeamsCaptions,
  `speakers=${!!g.__vexaTeamsSpeakers} captions=${!!g.__vexaTeamsCaptions}`);

await stop();
check('teardown: the entry still mid-refinement is flushed by the stop path, marked stable:false',
  captured.length === 2 && captured[1].name === 'Sven Olsen' && captured[1].stable === false,
  JSON.stringify(captured));

// A2: the caption AUTHOR is now offered to the lane as evidence for a transport TRACK — the name
// and the time only. Two things must still hold. The TEXT never crosses (it is Teams' ASR, not
// ours), and no caption becomes a naming HINT: the binder resolves per TURN over a window, which
// is precisely the race the track spine exists to leave behind.
check('the settled caption author reached the lane as track-name evidence',
  captionNames.length === 1 && captionNames[0].name === 'Priya Nair'
  && captionNames[0].tMs === captured[0]?.t, JSON.stringify(captionNames));
check('a caption still mid-refinement is NOT evidence — its author can still change',
  captionNames.every((c) => c.name !== 'Sven Olsen'), JSON.stringify(captionNames));
check('isolation: NO caption reached pipeline.recordHint — captions are not naming hints here',
  hints.length === 0, JSON.stringify(hints));

// ── The join-time enable flow (founder ruling: captions are switched ON at join) ──
{
  // A page whose toolbar never yields the More button: bounded retries, then ONE typed
  // captions-enable-failed observation — and no throw, because the join must not care.
  const logs: string[] = [];
  const deadPage = {
    async evaluate() { return false; },
    locator() { return { first: () => ({ async click() { throw new Error('no More button here'); } }) }; },
    async waitForTimeout() { /* instant */ },
    keyboard: { async press() { /* */ } },
  } as never;
  const result = await runTeamsCaptionEnable(deadPage, (m) => logs.push(m), 2, 0);
  check('enable: an unreachable toolbar ends as failed/more-button-unreachable, never a throw',
    result.outcome === 'failed' && result.reason === 'more-button-unreachable', JSON.stringify(result));
  const obsLine = logs.find((l) => l.includes('captions-enable-failed'));
  check('enable: failure is a TYPED captions-enable-failed observation on the caption log stream',
    !!obsLine && JSON.parse(obsLine.slice(obsLine.indexOf('{'))).attempts === 2, logs.join(' | '));
  check('enable: the fallback is stated — the DOM watcher carries on alone',
    logs.some((l) => l.includes('voice-level-outline watcher alone')), logs.join(' | '));
}
{
  // The guest menu shape from 0.10: More → a direct "Captions" item.
  const clicked: string[] = [];
  const menu = new El({ 'data-tid': 'menu' }, [
    new El({ role: 'menuitem' }, [], 'Captions'),
    new El({ role: 'menuitem' }, [], 'Language and speech'),
  ]);
  for (const item of menu.kids) {
    (item as any).offsetParent = menu;
    (item as any).click = () => clicked.push(item.textContent);
  }
  const savedDoc = g.document;
  g.document = { body: menu, querySelector: (s: string) => menu.querySelector(s), querySelectorAll: (s: string) => menu.querySelectorAll(s) };
  const guestPage = {
    async evaluate(fn: (arg: never) => unknown, arg?: unknown) { return fn(arg as never); },
    locator() { return { first: () => ({ async click() { /* menu opens */ } }) }; },
    async waitForTimeout() { /* instant */ },
    keyboard: { async press() { /* */ } },
  } as never;
  const result = await runTeamsCaptionEnable(guestPage, () => { /* quiet */ }, 1, 0);
  g.document = savedDoc;
  check('enable: the 0.10 guest path (More → "Captions") is taken and reported as clicked',
    result.outcome === 'clicked' && clicked.join() === 'Captions', `${JSON.stringify(result)} clicked=${clicked.join()}`);
}

(g as any).setInterval = realSetInterval;
(g as any).clearInterval = realClearInterval;

if (failed) { console.error(`\n❌ teams-caption-wiring: ${failed} checks FAILED.`); process.exit(1); }
console.log('\n✅ teams-caption-wiring: real bundle + real bridge carry Teams captions page→Node (dedup, epoch ms, teardown flush) and never into the name binder.');

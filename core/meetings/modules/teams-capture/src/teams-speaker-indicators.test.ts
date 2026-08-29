/**
 * teams-capture L2 — Gate A (coverage) + Gate B (candidate speaking indicators).
 *
 * Offline, no browser: an in-memory DOM shim, a hand-driven requestAnimationFrame
 * and an INJECTED clock, so the 60s indicator-silent window is exercised without
 * sleeping for a minute.
 *
 * What these pin:
 *   1. an outline whose inline style animates produces START then END
 *   2. a tile found WITHOUT an outline is reported `signal-absent` and never hinted
 *   3. `indicator-silent` fires once per window when observable > 0 and nothing speaks
 *   4. coverage accounting (found / observable / named / transitions) is correct
 *   5. direct outline/stream anchors remain discoverable without a matching outer tile
 *   6. REGRESSION — today's live production failure, pinned: 4 tiles, 1 outline,
 *      no indicator ever fires ⇒ 0 hints, 3 signal-absent, indicator-silent fires
 *
 * Run: npx tsx src/teams-speaker-indicators.test.ts
 */
import {
  createTeamsSpeakers,
  type TeamsProducerObservation,
  type TeamsSpeakers,
} from './msteams-speakers.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

// ── Minimal in-memory DOM shim (tag, .class, #id, [attr], [attr="v"],
//    [attr*="v"], [attr^="v"], compounds, comma lists) — no jsdom dependency ──
type Cond = (el: FakeEl) => boolean;
function simple(sel: string): Cond {
  sel = sel.trim();
  const attr = sel.match(/^\[([a-zA-Z0-9_-]+)(?:([*^]?=)"?([^"\]]*)"?)?\]$/);
  if (attr) {
    const [, name, op, val] = attr;
    return (el) => {
      const v = el.getAttribute(name);
      if (v == null) return false;
      if (!op) return true;
      if (op === '=') return v === val;
      if (op === '*=') return v.includes(val);
      if (op === '^=') return v.startsWith(val);
      return false;
    };
  }
  if (sel.startsWith('.')) { const c = sel.slice(1); return (el) => el.classList.contains(c); }
  if (sel.startsWith('#')) { const id = sel.slice(1); return (el) => el.getAttribute('id') === id; }
  if (sel === '*') return () => true;
  const tag = sel.toLowerCase();
  return (el) => el.tag === tag;
}
function compound(sel: string): Cond {
  const parts = sel.match(/(\[[^\]]*\]|[.#]?[a-zA-Z0-9_*-]+)/g) || [sel];
  const cs = parts.map(simple);
  return (el) => cs.every((c) => c(el));
}
function compile(selector: string): Cond {
  const gs = selector.split(',').map((s) => compound(s.trim()));
  return (el) => gs.some((g) => g(el));
}

class FakeEl {
  tag: string;
  attrs: Record<string, string>;
  kids: FakeEl[];
  ownText: string;
  parentElement: FakeEl | null = null;
  dataset: Record<string, string> = {};
  isConnected = true;
  constructor(tag: string, attrs: Record<string, string> = {}, kids: FakeEl[] = [], text = '') {
    this.tag = tag.toLowerCase(); this.attrs = attrs; this.kids = kids; this.ownText = text;
    for (const k of kids) k.parentElement = this;
  }
  get tagName(): string { return this.tag.toUpperCase(); }
  get children(): FakeEl[] { return this.kids; }
  get textContent(): string { let s = this.ownText; for (const k of this.kids) s += k.textContent; return s; }
  getAttribute(n: string): string | null { return n in this.attrs ? this.attrs[n] : null; }
  setAttribute(n: string, v: string): void { this.attrs[n] = v; }
  get classList() {
    const s = new Set((this.attrs['class'] || '').split(/\s+/).filter(Boolean));
    return { contains: (c: string) => s.has(c) };
  }
  matches(sel: string): boolean { return compile(sel)(this); }
  private desc(): FakeEl[] { const out: FakeEl[] = []; const w = (e: FakeEl): void => { for (const k of e.kids) { out.push(k); w(k); } }; w(this); return out; }
  querySelector(sel: string): FakeEl | null { const c = compile(sel); for (const d of this.desc()) if (c(d)) return d; return null; }
  querySelectorAll(sel: string): FakeEl[] { const c = compile(sel); return this.desc().filter(c); }
  closest(sel: string): FakeEl | null { const c = compile(sel); let cur: FakeEl | null = this; while (cur) { if (c(cur)) return cur; cur = cur.parentElement; } return null; }
}

const g = globalThis as any;
g.HTMLElement = FakeEl;
g.MutationObserver = class { observe(): void {} disconnect(): void {} };

let rafQueue: Array<() => void> = [];
g.requestAnimationFrame = (cb: () => void): number => { rafQueue.push(cb); return rafQueue.length; };
g.cancelAnimationFrame = (): void => {};
/** Drive one animation frame for every tile currently scheduled. */
function frame(): void { const pending = rafQueue; rafQueue = []; for (const cb of pending) cb(); }
const settle = (): Promise<void> => new Promise((r) => setTimeout(r, 5));

/** One participant tile: `[data-stream-type][data-tid="<name>"]` wrapper (Teams'
 *  stable name attribute) around an optional voice-level outline. */
function makeTile(
  id: string,
  name: string,
  opts: { outline: boolean },
): { tile: FakeEl; stream: FakeEl; outline: FakeEl | null } {
  const outline = opts.outline
    ? new FakeEl('div', { 'data-tid': 'voice-level-stream-outline', style: 'height: 0px;' })
    : null;
  const stream = new FakeEl('div', { 'data-tid': name, 'data-stream-type': 'Video' }, outline ? [outline] : []);
  const tile = new FakeEl('div', { 'data-tid': `participant-tile-${id}`, 'data-participant-id': id }, [stream]);
  return { tile, stream, outline };
}

function installDocument(
  tiles: FakeEl[],
  panelRows: FakeEl[] = [],
  opts: { streamWrappersOnly?: boolean; outlineAtomsOnly?: boolean } = {},
): FakeEl {
  const panel = panelRows.length ? new FakeEl('div', { 'data-tid': 'roster' }, panelRows) : null;
  const body = new FakeEl('body', {}, panel ? [...tiles, panel] : tiles);
  g.document = {
    body,
    querySelector: (s: string) => (s === '[role="main"]' ? body : null),
    querySelectorAll: (s: string) => {
      if (s === '[data-tid="voice-level-stream-outline"]') {
        if (opts.streamWrappersOnly) return [];
        return tiles.map((tile) => tile.matches(s) ? tile : tile.querySelector(s))
          .filter((el): el is FakeEl => !!el);
      }
      if (s === '[data-stream-type][data-tid]') {
        if (opts.outlineAtomsOnly) return [];
        return tiles.map((tile) => tile.matches(s) ? tile : tile.querySelector(s))
          .filter((el): el is FakeEl => !!el);
      }
      if (s === '[data-tid*="participant"]') {
        return opts.streamWrappersOnly || opts.outlineAtomsOnly ? [] : tiles;
      }
      if (panel && compile(s)(panel)) return [panel];
      return [];
    },
  };
  return body;
}

interface Harness {
  watcher: TeamsSpeakers;
  hints: Array<{ name: string; id: string; isEnd: boolean }>;
  observations: TeamsProducerObservation[];
  logs: string[];
  advance: (ms: number) => void;
}
function start(tiles: FakeEl[], overrides: Record<string, unknown> = {}): Harness {
  const {
    panelRows = [], streamWrappersOnly = false, outlineAtomsOnly = false, ...watcherOverrides
  } = overrides as {
    panelRows?: FakeEl[];
    streamWrappersOnly?: boolean;
    outlineAtomsOnly?: boolean;
  };
  installDocument(tiles, panelRows, { streamWrappersOnly, outlineAtomsOnly });
  rafQueue = [];
  let clock = 1_700_000_000_000;
  const hints: Array<{ name: string; id: string; isEnd: boolean }> = [];
  const observations: TeamsProducerObservation[] = [];
  const logs: string[] = [];
  const watcher = createTeamsSpeakers({
    debounceMs: 0,
    heartbeatMs: 100_000,
    indicatorSilentMs: 60_000,
    now: () => clock,
    log: (m) => logs.push(m),
    onSpeaking: (name, id, isEnd) => hints.push({ name, id, isEnd }),
    onObservation: (o) => observations.push(o),
    ...watcherOverrides,
  } as any);
  return { watcher, hints, observations, logs, advance: (ms) => { clock += ms; } };
}
const ofType = (observations: TeamsProducerObservation[], type: string): TeamsProducerObservation[] =>
  observations.filter((o) => o.type === type);

// ── 1. An outline whose inline style animates produces START then END ─────────
{
  const { tile, outline } = makeTile('alpha', 'Alpha Example', { outline: true });
  const h = start([tile]);
  await settle();

  check('animation: bootstrap emits no hint (a silent assertion is not speech)', h.hints.length === 0,
    JSON.stringify(h.hints));
  check('animation: bootstrap fires no indicator', ofType(h.observations, 'indicator-fired').length === 0);

  // A ONE-OFF style change is a layout reflow, not speech. It must not fire —
  // otherwise a single resize makes every tile read as speaking at once, which
  // is worse than silence: it attributes speech to the wrong person.
  h.advance(250);
  outline!.setAttribute('style', 'height: 2px;');
  frame();
  await settle();
  check('animation: a single one-off style change is not speech',
    h.hints.length === 0 && ofType(h.observations, 'indicator-fired').length === 0,
    JSON.stringify(h.hints));

  // The voice-level bar animates: the outline's inline style keeps moving off
  // its resting value. The observer already watched `style`; now the detector does.
  h.advance(250);
  outline!.setAttribute('style', 'height: 14px; transform: scaleY(0.7);');
  frame();
  await settle();

  const fired = ofType(h.observations, 'indicator-fired') as any[];
  check('animation: START emitted', h.hints.length === 1 && h.hints[0].isEnd === false, JSON.stringify(h.hints));
  check('animation: START carries the resolved name', h.hints[0]?.name === 'Alpha Example');
  check('animation: indicator-fired names inline-style-motion',
    fired.length === 1 && fired[0].indicator === 'inline-style-motion', JSON.stringify(fired));

  // Speaking is a LEVEL, not an edge. A sample observing no change is the gap
  // BETWEEN two voice-bar updates, not the end of the turn, so the edge is held
  // for indicatorHoldMs. Reading it raw is exactly what killed the hint path:
  // the level flapped at the sampling rate and the debounce cancelled every
  // pending emit.
  h.advance(250);
  frame();
  await settle();
  check('animation: a quiet sample inside the hold does NOT end the turn',
    h.hints.length === 1, JSON.stringify(h.hints));

  // Speech stops: the hold expires, the level drops, the 200ms hysteresis
  // admits the closing transition.
  h.advance(500);
  frame();
  await settle();

  check('animation: END emitted once the hold expires',
    h.hints.length === 2 && h.hints[1].isEnd === true, JSON.stringify(h.hints));
  check('animation: END carries the same name', h.hints[1]?.name === 'Alpha Example');
  check('animation: health reports one speaking transition', h.watcher.health().transitions === 1,
    JSON.stringify(h.watcher.health()));
  h.watcher.destroy();
}

// ── 1a. A human whose name contains the configured bot name is still a human ─
{
  const { tile, outline } = makeTile('vexa-human', 'Vexa Petrova', { outline: true });
  const h = start([tile], { selfName: 'Vexa' });
  await settle();
  h.advance(250);
  outline!.setAttribute('style', 'height: 2px;');
  frame();
  await settle();
  h.advance(250);
  outline!.setAttribute('style', 'height: 14px; transform: scaleY(0.7);');
  frame();
  await settle();
  check('exact self filtering preserves a real human whose name contains the bot name',
    h.hints.length === 1 && h.hints[0]?.name === 'Vexa Petrova' && !h.hints[0]?.isEnd,
    JSON.stringify(h.hints));
  h.watcher.destroy();
}

// ── 1b. aria-state candidate, and the "not speaking" trap it must not fall into ─
{
  const { tile, outline } = makeTile('aria', 'Alpha Example', { outline: true });
  const h = start([tile]);
  await settle();

  // A label that CONTAINS "speaking" but negates it must not fire.
  h.advance(250);
  tile.setAttribute('aria-label', 'Alpha Example, not speaking');
  frame();
  await settle();
  check('aria-state: "not speaking" does not fire', h.hints.length === 0, JSON.stringify(h.hints));

  h.advance(250);
  outline!.setAttribute('aria-pressed', 'true');
  frame();
  await settle();
  const fired = ofType(h.observations, 'indicator-fired') as any[];
  check('aria-state: aria-pressed on the outline fires and is named',
    fired.length === 1 && fired[0].indicator === 'aria-state', JSON.stringify(fired));
  check('aria-state: START emitted', h.hints.length === 1 && h.hints[0].isEnd === false, JSON.stringify(h.hints));
  h.watcher.destroy();
}

// ── 1c. class-token-delta candidate — records WHICH token, bounded ────────────
{
  const { tile, outline } = makeTile('token', 'Alpha Example', { outline: true });
  const h = start([tile]);
  await settle();

  h.advance(250);
  outline!.setAttribute('class', '___speaking-1a2b');
  frame();
  await settle();
  const fired = ofType(h.observations, 'indicator-fired') as any[];
  check('class-token-delta: an added class token fires',
    fired.length === 1 && fired[0].indicator === 'class-token-delta', JSON.stringify(fired));
  check('class-token-delta: reports WHICH token, sanitized and bounded',
    fired[0]?.detail === '___speaking-1a2b', JSON.stringify(fired[0]));
  check('class-token-delta: START emitted', h.hints.length === 1 && h.hints[0].isEnd === false);

  // Removing the token is not activity — only additions count. Like every edge
  // candidate this one is held, so the turn closes when the hold expires.
  h.advance(250);
  outline!.setAttribute('class', '');
  frame();
  await settle();
  check('class-token-delta: a removed token does not end the turn inside the hold',
    h.hints.length === 1, JSON.stringify(h.hints));
  h.advance(500);
  frame();
  await settle();
  check('class-token-delta: the turn closes once the hold expires',
    h.hints.length === 2 && h.hints[1].isEnd === true, JSON.stringify(h.hints));
  check('class-token-delta: still exactly one indicator-fired',
    ofType(h.observations, 'indicator-fired').length === 1);
  h.watcher.destroy();
}

// ── 2. A tile found WITHOUT an outline: reported, never hinted ────────────────
{
  const observable = makeTile('with-signal', 'Alpha Example', { outline: true });
  const blind = makeTile('no-signal', 'Beta Example', { outline: false });
  const h = start([observable.tile, blind.tile]);
  await settle();

  const absent = ofType(h.observations, 'signal-absent') as any[];
  check('signal-absent: exactly one observation for the outline-less tile', absent.length === 1,
    JSON.stringify(absent));
  check('signal-absent: typed shape is exactly the contract',
    absent[0]?.type === 'signal-absent' && absent[0]?.platform === 'teams'
    && absent[0]?.signal === 'dom-outline' && absent[0]?.reason === 'outline-missing'
    && Number.isFinite(absent[0]?.tMs), JSON.stringify(absent[0]));
  check('signal-absent: carries no display name and no DOM text',
    !!absent[0] && !JSON.stringify(absent[0]).includes('Beta') && Object.keys(absent[0]).length === 5,
    JSON.stringify(absent[0]));
  check('signal-absent: logs a value-free structural fingerprint',
    h.logs.some((line) => line.includes('stream=descendant stable-root=yes'))
      && h.logs.every((line) => !line.includes('Beta Example')),
    JSON.stringify(h.logs));
  check('signal-absent: produces no hint', h.hints.length === 0, JSON.stringify(h.hints));

  // Even when the blind tile's markup churns, it stays unobservable and unhinted.
  h.advance(250);
  blind.tile.setAttribute('class', 'some-new-class');
  frame();
  await settle();
  check('signal-absent: a tile with no outline never produces a hint', h.hints.length === 0);
  check('signal-absent: one observation per tile, not one per rescan',
    ofType(h.observations, 'signal-absent').length === 1);
  h.watcher.destroy();
}

// ── 3. indicator-silent fires when observable > 0 and nothing happens ─────────
{
  const { tile } = makeTile('quiet', 'Alpha Example', { outline: true });
  const h = start([tile], { heartbeatMs: 5 });
  await settle();
  check('indicator-silent: does not fire inside the window',
    ofType(h.observations, 'indicator-silent').length === 0);

  h.advance(61_000);                     // injected clock — no sleeping
  await new Promise((r) => setTimeout(r, 40));
  const silent = ofType(h.observations, 'indicator-silent') as any[];
  check('indicator-silent: fires once past the window', silent.length === 1, JSON.stringify(silent));
  check('indicator-silent: typed shape carries the coverage it is complaining about',
    silent[0]?.type === 'indicator-silent' && silent[0]?.platform === 'teams'
    && silent[0]?.signal === 'dom-outline' && silent[0]?.reason === 'no-speaking-transition-in-window'
    && silent[0]?.found === 1 && silent[0]?.observable === 1 && silent[0]?.windowMs === 60_000,
    JSON.stringify(silent[0]));
  check('indicator-silent: logs at WARN',
    h.logs.some((l) => l.includes('WARN indicator-silent')), JSON.stringify(h.logs));
  check('indicator-silent: never becomes a hint', h.hints.length === 0, JSON.stringify(h.hints));

  await new Promise((r) => setTimeout(r, 40));
  check('indicator-silent: one per window, not one per heartbeat',
    ofType(h.observations, 'indicator-silent').length === 1);

  h.advance(61_000);
  await new Promise((r) => setTimeout(r, 40));
  check('indicator-silent: re-fires on the next window',
    ofType(h.observations, 'indicator-silent').length === 2);
  h.watcher.destroy();
}

// ── 4. Coverage accounting ───────────────────────────────────────────────────
{
  const tiles = [
    makeTile('a', 'Alpha Example', { outline: true }).tile,
    makeTile('b', 'Beta Example', { outline: true }).tile,
    makeTile('c', 'Gamma Example', { outline: false }).tile,
  ];
  const h = start(tiles);
  await settle();
  const health = h.watcher.health();
  check('coverage: found counts every matched tile', health.found === 3, JSON.stringify(health));
  check('coverage: observable counts only tiles carrying the outline', health.observable === 2, JSON.stringify(health));
  check('coverage: named counts observed tiles whose name resolved', health.named === 2, JSON.stringify(health));
  check('coverage: nameUnresolved is zero when both names resolve', health.nameUnresolved === 0, JSON.stringify(health));
  check('coverage: transitions starts at zero', health.transitions === 0, JSON.stringify(health));
  check('coverage: the scan line reports the absent tiles',
    h.logs.some((l) => l.includes('Scanned 3 participants, observing 2 with signal (signal-absent 1)')),
    JSON.stringify(h.logs));
  check('coverage: no coverage-low WARN at 2/3', !h.logs.some((l) => l.includes('WARN coverage-low')));
  h.watcher.destroy();
}

// ── 4a. The exact speaking atom is independently discoverable ────────────────
// The outline itself is the strongest layout-independent anchor. Its ancestor
// stream wrapper supplies the name even when no outer tile selector matches.
{
  const { tile, outline } = makeTile('outline-dmitry', 'Dmitry Grankin', { outline: true });
  const h = start([tile], { outlineAtomsOnly: true });
  await settle();

  const health = h.watcher.health();
  check('outline-only layout: canonical participant is found once',
    health.found === 1 && health.observable === 1 && health.named === 1,
    JSON.stringify(health));
  h.advance(250);
  outline!.setAttribute('style', 'height: 2px;');
  frame();
  await settle();
  h.advance(250);
  outline!.setAttribute('style', 'height: 14px; transform: scaleY(0.7);');
  frame();
  await settle();
  check('outline-only layout: speaking atom joins to the stream-wrapper name',
    h.hints.length === 1 && h.hints[0]?.name === 'Dmitry Grankin' && !h.hints[0]?.isEnd,
    JSON.stringify(h.hints));
  h.watcher.destroy();
}

// The outline can also survive a layout that omits the stream wrapper. In that
// shape, climb to a stable participant root and use the guarded visible-name
// leaf fallback; the speaking atom still must never become a nameless hint.
{
  const nameLeaf = new FakeEl('div', {}, [], 'Dmitry Grankin');
  const outline = new FakeEl('div', {
    'data-tid': 'voice-level-stream-outline', style: 'height: 0px;',
  });
  const stableRoot = new FakeEl('div', { 'data-participant-id': 'dmitry-no-stream' }, [
    nameLeaf, outline,
  ]);
  const h = start([stableRoot], { outlineAtomsOnly: true });
  await settle();

  check('outline-only fallback: stable root supplies the guarded visible name',
    h.watcher.health().found === 1 && h.watcher.health().named === 1,
    JSON.stringify(h.watcher.health()));
  h.advance(250);
  outline.setAttribute('style', 'height: 2px;');
  frame();
  await settle();
  h.advance(250);
  outline.setAttribute('style', 'height: 14px; transform: scaleY(0.7);');
  frame();
  await settle();
  check('outline-only fallback: speaking evidence emits the visible human name',
    h.hints.length === 1 && h.hints[0]?.name === 'Dmitry Grankin', JSON.stringify(h.hints));
  h.watcher.destroy();
}

// ── 4b. The stable stream wrapper is independently discoverable ──────────────
// Teams can render the visible name and active-speaker outline inside a stream
// wrapper while the surrounding participant tile no longer matches any of the
// broad layout selectors. The wrapper is sufficient evidence by itself.
{
  const { tile, outline } = makeTile('dmitry', 'Dmitry Grankin', { outline: true });
  const h = start([tile], { streamWrappersOnly: true });
  await settle();

  const health = h.watcher.health();
  check('stream-only layout: canonical wrapper is found once',
    health.found === 1 && health.observable === 1 && health.named === 1,
    JSON.stringify(health));
  const rosterNames = ofType(h.observations, 'roster-name') as any[];
  check('stream-only layout: stable data-tid yields the human name',
    rosterNames.some((o) => o.name === 'Dmitry Grankin'), JSON.stringify(rosterNames));

  h.advance(250);
  outline!.setAttribute('style', 'height: 2px;');
  frame();
  await settle();
  h.advance(250);
  outline!.setAttribute('style', 'height: 14px; transform: scaleY(0.7);');
  frame();
  await settle();
  check('stream-only layout: speaking evidence emits the resolved name',
    h.hints.length === 1 && h.hints[0]?.name === 'Dmitry Grankin' && !h.hints[0]?.isEnd,
    JSON.stringify(h.hints));
  h.watcher.destroy();
}

// Two stream wrappers may legitimately expose the same display name. Without a
// participant ID, their element identity remains distinct; the human-readable
// data-tid must never collapse their speaking state into one participant.
{
  const outlineA = new FakeEl('div', {
    'data-tid': 'voice-level-stream-outline', style: 'height: 0px;',
  });
  const outlineB = new FakeEl('div', {
    'data-tid': 'voice-level-stream-outline', style: 'height: 0px;',
  });
  const streamA = new FakeEl('div', {
    'data-tid': 'Alex Smith', 'data-stream-type': 'Video',
  }, [outlineA]);
  const streamB = new FakeEl('div', {
    'data-tid': 'Alex Smith', 'data-stream-type': 'Video',
  }, [outlineB]);
  const h = start([streamA, streamB], { streamWrappersOnly: true });
  await settle();

  check('same-name streams: both canonical participants are retained',
    h.watcher.health().found === 2 && h.watcher.health().observable === 2,
    JSON.stringify(h.watcher.health()));
  h.advance(250);
  outlineA.setAttribute('style', 'height: 2px;');
  frame();
  await settle();
  h.advance(250);
  outlineA.setAttribute('style', 'height: 14px; transform: scaleY(0.7);');
  frame();
  await settle();
  h.advance(250);
  outlineB.setAttribute('style', 'height: 2px;');
  frame();
  await settle();
  h.advance(250);
  outlineB.setAttribute('style', 'height: 14px; transform: scaleY(0.7);');
  frame();
  await settle();
  const starts = h.hints.filter((hint) => !hint.isEnd);
  check('same-name streams: speaking state uses two distinct IDs',
    starts.length === 2 && starts[0]?.name === 'Alex Smith' && starts[1]?.name === 'Alex Smith'
    && starts[0]?.id !== starts[1]?.id,
    JSON.stringify(starts));
  h.watcher.destroy();
}

// ── 5. REGRESSION — the measured production failure, pinned ──────────────────
// Live 13-minute Teams meeting on v0.12.18: "Scanned 4 participants, observing 1
// with signal" ×127, SPEAKER_START 0, all 37 transcript rows labelled "Speaker".
// Three of four participants were invisible and the detector never fired once.
// This is what that run must look like now: still zero hints (we do not invent
// names), but the module says out loud what it cannot see.
{
  const tiles = [
    makeTile('p1', 'Alpha Example', { outline: true }).tile,
    makeTile('p2', 'Beta Example', { outline: false }).tile,
    makeTile('p3', 'Gamma Example', { outline: false }).tile,
    makeTile('p4', 'Delta Example', { outline: false }).tile,
  ];
  const h = start(tiles, { heartbeatMs: 5 });
  await settle();

  // A whole meeting's worth of frames in which nothing about the DOM changes.
  for (let i = 0; i < 20; i++) { h.advance(250); frame(); }
  await settle();

  const health = h.watcher.health();
  check('regression: coverage matches the live scan line (4 found, 1 observable)',
    health.found === 4 && health.observable === 1, JSON.stringify(health));
  check('regression: zero hints — unknown stays unknown, no name is invented',
    h.hints.length === 0, JSON.stringify(h.hints));
  check('regression: zero speaking transitions', health.transitions === 0, JSON.stringify(health));
  check('regression: three signal-absent observations, one per unobservable tile',
    ofType(h.observations, 'signal-absent').length === 3,
    JSON.stringify(ofType(h.observations, 'signal-absent')));
  check('regression: no indicator ever fired', ofType(h.observations, 'indicator-fired').length === 0);
  check('regression: coverage-low WARN at 1/4',
    h.logs.some((l) => l.includes('WARN coverage-low found=4 observable=1')), JSON.stringify(h.logs));

  h.advance(61_000);
  await new Promise((r) => setTimeout(r, 40));
  check('regression: indicator-silent fires — the module reports its own failure',
    ofType(h.observations, 'indicator-silent').length === 1,
    JSON.stringify(ofType(h.observations, 'indicator-silent')));
  check('regression: diagnostics never crossed into the hint stream', h.hints.length === 0);
  h.watcher.destroy();
}

// ── 5b. ROSTER COMPLETENESS — meeting 26218's false 1/1 is forbidden ─────────
{
  // Teams can render one participant on more than one DOM surface. Every surface resolves a name,
  // so four elements representing two people are complete 2/2 rather than the old broken 2/4.
  const tiles = [
    makeTile('alpha-tile', 'Alpha Example', { outline: true }).tile,
    makeTile('alpha-row', 'Alpha Example', { outline: false }).tile,
    makeTile('beta-tile', 'Beta Example', { outline: true }).tile,
    makeTile('beta-row', 'Beta Example', { outline: false }).tile,
  ];
  const h = start(tiles, { selfName: 'Vexa' });
  await settle();
  const rosterCoverage = ofType(h.observations, 'roster-coverage') as any[];
  const last = rosterCoverage[rosterCoverage.length - 1];
  check('coverage dedupes named duplicate surfaces into two complete participants',
    last?.named === 2 && last?.participants === 2, JSON.stringify(rosterCoverage));
  h.watcher.destroy();
}
{
  const panelRows = [
    new FakeEl('div', { role: 'treeitem' }, [new FakeEl('span', {}, [], 'Dmitry Grankin')]),
    new FakeEl('div', { role: 'treeitem' }, [new FakeEl('span', {}, [], 'mic_off')]),
  ];
  const h = start([], { selfName: 'Vexa', panelRows });
  await settle();
  const rosterCoverage = ofType(h.observations, 'roster-coverage') as any[];
  const last = rosterCoverage[rosterCoverage.length - 1];
  check('an unresolved roster-panel row keeps one readable name at incomplete 1/2',
    last?.named === 1 && last?.participants === 2, JSON.stringify(rosterCoverage));
  h.watcher.destroy();
}
{
  const tiles = [
    makeTile('p1', 'Dmitry Grankin', { outline: true }).tile,
    makeTile('p2', 'video-stream-2', { outline: false }).tile,
    makeTile('p3', 'participant-tile-3', { outline: false }).tile,
    makeTile('p4', 'voice-level-stream-outline', { outline: false }).tile,
  ];
  const h = start(tiles, { selfName: 'Vexa' });
  await settle();
  const rosterCoverage = ofType(h.observations, 'roster-coverage') as any[];
  const last = rosterCoverage[rosterCoverage.length - 1];
  check('m26218 coverage: one resolved name across four matched participants is 1/4, never 1/1',
    last?.named === 1 && last?.participants === 4, JSON.stringify(rosterCoverage));
  h.watcher.destroy();
}
{
  const tiles = [
    makeTile('p1', 'VexaBot-8f264c (Unverified)', { outline: true }).tile,
    makeTile('p2', 'video-stream-2', { outline: false }).tile,
    makeTile('p3', 'participant-tile-3', { outline: false }).tile,
    makeTile('p4', 'voice-level-stream-outline', { outline: false }).tile,
  ];
  const h = start(tiles, { selfName: 'Vexa' });
  await settle();
  const rosterCoverage = ofType(h.observations, 'roster-coverage') as any[];
  const rosterNames = ofType(h.observations, 'roster-name') as any[];
  const last = rosterCoverage[rosterCoverage.length - 1];
  check('m26218 generated bot: it never enters the roster-name stream',
    rosterNames.every((o) => o.name !== 'VexaBot-8f264c (Unverified)'), JSON.stringify(rosterNames));
  check('m26218 generated bot: unresolved four-surface scan reports 0/4 and cannot eliminate',
    last?.named === 0 && last?.participants === 4, JSON.stringify(rosterCoverage));
  h.watcher.destroy();
}
{
  // WHEN THE BOT NAME IS THE GENERATED ONE, THE BOT IS STILL THE BOT. `VexaBot-<hex>` is what
  // meeting-api mints when the caller omits bot_name, and the candidate guard refuses that shape by
  // design — so self must be decided before the guard runs. Decided after it, our own tile resolves
  // to no name, counts as one unresolved participant, and holds the room at named < participants
  // forever, which is exactly the condition that switches elimination off for the humans.
  const tiles = [
    makeTile('self', 'VexaBot-a1b2c3 (Unverified)', { outline: true }).tile,
    makeTile('p1', 'Dmitry Grankin', { outline: true }).tile,
    makeTile('p2', 'Priya Nair', { outline: false }).tile,
  ];
  const h = start(tiles, { selfName: 'VexaBot-a1b2c3' });
  await settle();
  const rosterCoverage = ofType(h.observations, 'roster-coverage') as any[];
  const rosterNames = ofType(h.observations, 'roster-name') as any[];
  const last = rosterCoverage[rosterCoverage.length - 1];
  check('generated self name: our own tile never enters the roster-name stream',
    rosterNames.every((o) => !String(o.name).toLowerCase().startsWith('vexabot')),
    JSON.stringify(rosterNames));
  check('generated self name: our own tile is excluded from named AND from unresolved — 2/2 eliminates',
    last?.named === 2 && last?.participants === 2, JSON.stringify(rosterCoverage));
  h.watcher.destroy();
}
{
  // The same bot, seen on the ROSTER PANEL rather than a tile: a self row is not a participant on
  // either surface, and the row that cannot be resolved must not be counted as somebody unnamed.
  const panelRows = [
    new FakeEl('div', { role: 'treeitem' }, [new FakeEl('span', {}, [], 'VexaBot-a1b2c3 (Unverified)')]),
    new FakeEl('div', { role: 'treeitem' }, [new FakeEl('span', {}, [], 'Dmitry Grankin')]),
  ];
  const h = start([], { selfName: 'VexaBot-a1b2c3', panelRows });
  await settle();
  const rosterCoverage = ofType(h.observations, 'roster-coverage') as any[];
  const last = rosterCoverage[rosterCoverage.length - 1];
  check('generated self name: our own roster-panel row is neither named nor counted — 1/1',
    last?.named === 1 && last?.participants === 1, JSON.stringify(rosterCoverage));
  h.watcher.destroy();
}
{
  // Roster corroboration end-to-end: a participant whose display name is genuinely bare lowercase
  // enters the roster because the PANEL lists them, while a bare label present only on a tile does
  // not. Both surfaces are read in one scan, and only one of them is name-authoritative.
  const tiles = [
    makeTile('p1', 'leo', { outline: true }).tile,
    makeTile('p2', 'datenanalyse', { outline: false }).tile,
  ];
  const panelRows = [new FakeEl('div', { role: 'treeitem' }, [new FakeEl('span', {}, [], 'leo')])];
  const h = start(tiles, { selfName: 'Vexa', panelRows });
  await settle();
  const rosterNames = ofType(h.observations, 'roster-name') as any[];
  check('roster corroboration: a roster-listed bare lowercase name reaches the roster stream',
    rosterNames.some((o) => o.name === 'leo'), JSON.stringify(rosterNames));
  check('roster corroboration: a bare label seen only on a tile is still refused',
    rosterNames.every((o) => o.name !== 'datenanalyse'), JSON.stringify(rosterNames));
  h.watcher.destroy();
}

// ── 6. REALISTIC CADENCE — the shape every other block above was blind to ────
// Every check above drives exactly ONE sample per DOM change. A real page does
// not: rAF samples at ~60fps while a voice bar updates every ~150ms, so ~8 of
// every 9 samples observe no change at all. That gap is where the hint path
// died — the level flapped, and because the 300ms debounce is longer than the
// 200ms hysteresis, each pending emit was cancelled by the opposite transition.
// Measured before the fix, here and in headless Chromium alike: transitions=3,
// indicator-fired x3, onSpeaking never called.
//
// So this block uses REAL timers, a REAL 16ms frame interval and the REAL 300ms
// debounce and wall clock. It is the only block whose failure mode matches
// production, which is why it does not get to use the injected clock.
{
  const { tile, outline } = makeTile('cadence', 'Alice Real', { outline: true });
  installDocument([tile]);
  rafQueue = [];
  const hints: Array<{ name: string; isEnd: boolean }> = [];
  const observations: TeamsProducerObservation[] = [];
  const logs: string[] = [];
  const frameTimer = setInterval(frame, 16);          // the ~60fps sampler
  const watcher = createTeamsSpeakers({
    selfName: 'Vexa',                                  // default debounceMs (300)
    heartbeatMs: 100_000,
    indicatorSilentMs: 600_000,
    log: (m) => logs.push(m),
    onSpeaking: (name, _id, isEnd) => hints.push({ name, isEnd }),
    onObservation: (o) => observations.push(o),
  });
  const wait = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

  await wait(700);
  for (let i = 0; i < 10; i++) {                       // voice bar: 10 updates @150ms
    outline!.setAttribute('style', `transform:scaleY(${(0.2 + i * 0.07).toFixed(2)})`);
    await wait(150);
  }
  await wait(1500);                                    // silence
  clearInterval(frameTimer);

  const starts = hints.filter((h) => !h.isEnd);
  const ends = hints.filter((h) => h.isEnd);
  check('cadence: exactly one START crossed onSpeaking', starts.length === 1, JSON.stringify(hints));
  check('cadence: exactly one END crossed onSpeaking', ends.length === 1, JSON.stringify(hints));
  check('cadence: START precedes END', hints[0]?.isEnd === false && hints[1]?.isEnd === true, JSON.stringify(hints));
  check('cadence: the hint carries the resolved name', hints.every((h) => h.name === 'Alice Real'), JSON.stringify(hints));
  check('cadence: SPEAKER_START reached the log', logs.some((l) => l.includes('SPEAKER_START')), JSON.stringify(logs.filter((l) => l.includes('SPEAKER'))));
  check('cadence: the turn is ONE transition, not one per bar update',
    watcher.health().transitions === 1, JSON.stringify(watcher.health()));
  check('cadence: one indicator-fired, naming inline-style-motion',
    ofType(observations, 'indicator-fired').length === 1
    && (ofType(observations, 'indicator-fired')[0] as any).indicator === 'inline-style-motion',
    JSON.stringify(ofType(observations, 'indicator-fired')));
  watcher.destroy();
}

if (failed) { console.error(`\n❌ teams speaker indicators: ${failed} checks FAILED.`); process.exit(1); }
console.log('\n✅ teams speaker indicators: coverage is accounted for, candidates are named, silence is reported.');

/**
 * teams-captions L2 — the caption reader's PURE logic, no browser. Drives the
 * real createTeamsCaptions against an in-memory DOM shim (the same shim style as
 * teams-capture.test.ts) with an injected clock and a manual `scanNow()` pump, so
 * stabilization is proven WITHOUT wall-clock sleeps.
 *
 * What is pinned here is exactly what the first live meeting cannot tell us and
 * what a live meeting must not be spent re-discovering:
 *   • the 0.10 caption DOM (wrapper + [data-tid=author] + [data-tid=closed-caption-text])
 *     is read, paired by document order, host-view AND guest-view shapes;
 *   • incremental refinement collapses to ONE emission on stabilization —
 *     never one per keystroke-level mutation;
 *   • an empty / non-name-shaped author WITHHOLDS the event and says so;
 *   • no wrapper ⇒ exactly one `captions-absent` and NO caption events;
 *   • captions Teams attributes to us (the bot) are dropped;
 *   • CC is FLAKE-CLASS: a renderer that disappears mid-meeting emits
 *     `captions-lost`, one that comes back emits `captions-recovered`, and the
 *     reader keeps working across the gap — the founder's ruling that captions
 *     are unreliable at any moment, written as a test rather than a hope.
 * Run: npm test  or  npx tsx src/teams-captions.test.ts
 */
import {
  createTeamsCaptions,
  teamsCaptionSelectors,
  type TeamsCaptionEvent,
  type TeamsCaptionObservation,
} from './index.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

// ── Minimal in-memory DOM shim (tag, .class, #id, [attr], [attr="v"], [attr*="v"],
//    [attr^="v"], comma lists) — no jsdom dependency. Mirrors teams-capture.test.ts.
type Cond = (el: FakeEl) => boolean;
function simple(sel: string): Cond {
  sel = sel.trim();
  const attr = sel.match(/^\[([a-zA-Z0-9_-]+)(?:([*^]?=)"?([^"\]]*)"?)?\]$/);
  if (attr) {
    const [, name, op, val] = attr;
    return (el) => { const v = el.getAttribute(name); if (v == null) return false; if (!op) return true;
      if (op === '=') return v === val; if (op === '*=') return v.includes(val); if (op === '^=') return v.startsWith(val); return false; };
  }
  if (sel.startsWith('.')) { const c = sel.slice(1); return (el) => el.classList.contains(c); }
  if (sel.startsWith('#')) { const id = sel.slice(1); return (el) => el.getAttribute('id') === id; }
  if (sel === '*') return () => true;
  const tag = sel.toLowerCase();
  return (el) => el.tag === tag;
}
function compound(sel: string): Cond { const parts = sel.match(/(\[[^\]]*\]|[.#]?[a-zA-Z0-9_*-]+)/g) || [sel]; const cs = parts.map(simple); return (el) => cs.every((c) => c(el)); }
function compile(selector: string): Cond { const gs = selector.split(',').map((s) => compound(s.trim())); return (el) => gs.some((g) => g(el)); }

class FakeEl {
  tag: string; attrs: Record<string, string>; ownText: string; kids: FakeEl[]; parentElement: FakeEl | null = null;
  constructor(tag: string, attrs: Record<string, string> = {}, kids: FakeEl[] = [], text = '') {
    this.tag = tag.toLowerCase(); this.attrs = attrs; this.kids = kids; this.ownText = text;
    for (const k of kids) k.parentElement = this;
  }
  get tagName(): string { return this.tag.toUpperCase(); }
  get childElementCount(): number { return this.kids.length; }
  get textContent(): string { let s = this.ownText; for (const k of this.kids) s += k.textContent; return s; }
  set text(v: string) { this.ownText = v; }
  getAttribute(n: string): string | null { return n in this.attrs ? this.attrs[n] : null; }
  get classList() { const s = new Set((this.attrs['class'] || '').split(/\s+/).filter(Boolean)); return { contains: (c: string) => s.has(c) }; }
  matches(sel: string): boolean { return compile(sel)(this); }
  append(child: FakeEl): void { child.parentElement = this; this.kids.push(child); }
  private desc(): FakeEl[] { const out: FakeEl[] = []; const w = (e: FakeEl) => { for (const k of e.kids) { out.push(k); w(k); } }; w(this); return out; }
  querySelector(sel: string): FakeEl | null { const c = compile(sel); for (const d of this.desc()) if (c(d)) return d; return null; }
  querySelectorAll(sel: string): FakeEl[] { const c = compile(sel); return this.desc().filter(c); }
  closest(sel: string): FakeEl | null { const c = compile(sel); let cur: FakeEl | null = this; while (cur) { if (c(cur)) return cur; cur = cur.parentElement; } return null; }
}
const e = (tag: string, attrs: Record<string, string> = {}, kids: FakeEl[] = []) => new FakeEl(tag, attrs, kids);
const t = (tag: string, text: string, attrs: Record<string, string> = {}) => new FakeEl(tag, attrs, [], text);
function makeDocument(root: FakeEl) {
  const all = () => { const out: FakeEl[] = [root]; const w = (el: FakeEl) => { for (const k of el.kids) { out.push(k); w(k); } }; w(root); return out; };
  return { body: root, querySelector: (s: string) => all().find(compile(s)) || null, querySelectorAll: (s: string) => all().filter(compile(s)) };
}

const g = globalThis as any;
g.MutationObserver = class { observe() {} disconnect() {} };
g.window = { setInterval: () => 1 as any, clearInterval: () => {} };
g.setInterval = () => 1 as any; g.clearInterval = () => {};
const setDoc = (root: FakeEl) => { g.document = makeDocument(root); };

// ── Caption DOM fixtures, from the 0.10 selectors (verified live 2026-03-19) ──
// HOST view interposes the items-renderer the GUEST view lacks; only the wrapper
// and the two leaf atoms are common, which is why pairing is by document order.
const captionRow = (author: string, text: string) => e('div', { class: 'row' }, [
  t('span', author, { 'data-tid': 'author' }),
  t('span', text, { 'data-tid': 'closed-caption-text' }),
]);
function hostWrapper(rows: FakeEl[]): FakeEl {
  return e('div', { 'data-tid': 'closed-caption-renderer-wrapper' }, [
    e('div', { 'data-tid': 'closed-caption-v2-virtual-list-content' }, [
      e('div', { 'data-tid': 'closed-captions-v2-items-renderer' }, rows),
    ]),
  ]);
}
function guestWrapper(rows: FakeEl[]): FakeEl {
  return e('div', { 'data-tid': 'closed-caption-renderer-wrapper' }, [
    e('div', { 'data-tid': 'closed-caption-v2-virtual-list-content' }, rows),
  ]);
}

interface Harness {
  captions: TeamsCaptionEvent[];
  observations: TeamsCaptionObservation[];
  tick(ms: number): void;
  scan(): void;
  destroy(): void;
}
function harness(root: FakeEl, opts: { selfName?: string; stabilizeMs?: number; absentAfterMs?: number } = {}): Harness {
  setDoc(root);
  let clock = 1_000_000;              // an epoch-shaped fixed clock
  const captions: TeamsCaptionEvent[] = [];
  const observations: TeamsCaptionObservation[] = [];
  const cc = createTeamsCaptions({
    onCaption: (c) => captions.push(c),
    onObservation: (o) => observations.push(o),
    now: () => clock,
    selfName: opts.selfName,
    stabilizeMs: opts.stabilizeMs ?? 900,
    absentAfterMs: opts.absentAfterMs ?? 30_000,
  });
  return {
    captions,
    observations,
    tick(ms: number) { clock += ms; cc.scanNow(); },
    scan() { cc.scanNow(); },
    destroy() { cc.destroy(); },
  };
}

// ── Exported selector surface — the 0.10 atoms must stay first ────────────────
check('wrapper candidates lead with the 0.10 renderer wrapper',
  teamsCaptionSelectors.wrappers[0] === '[data-tid="closed-caption-renderer-wrapper"]');
check('author candidates lead with [data-tid="author"]',
  teamsCaptionSelectors.authors[0] === '[data-tid="author"]');
check('text candidates lead with [data-tid="closed-caption-text"]',
  teamsCaptionSelectors.texts[0] === '[data-tid="closed-caption-text"]');

// ── 1. Host view: an entry emits ONCE, on stabilization ──────────────────────
{
  const rows = [captionRow('Priya Nair', 'we should')];
  const h = harness(e('body', {}, [hostWrapper(rows)]));
  check('host: captions-active emitted on first scan',
    h.observations.some((o) => o.type === 'captions-active'), JSON.stringify(h.observations));
  const found = h.observations.find((o) => o.type === 'captions-active') as any;
  check('host: captions-active names the matched wrapper + atoms',
    found?.wrapperSelector === '[data-tid="closed-caption-renderer-wrapper"]'
    && found?.authorSelector === '[data-tid="author"]'
    && found?.textSelector === '[data-tid="closed-caption-text"]', JSON.stringify(found));
  check('host: nothing emitted while the entry is still refining', h.captions.length === 0);

  // Teams refines the SAME entry in place — word growth then re-punctuation.
  rows[0].kids[1].text = 'we should ship';        h.tick(120);
  rows[0].kids[1].text = 'we should ship the';    h.tick(120);
  rows[0].kids[1].text = 'We should ship the CC lane.'; h.tick(120);
  check('host: still nothing — refinement is not an emission', h.captions.length === 0, JSON.stringify(h.captions));

  h.tick(1000);   // now it has stopped changing for > stabilizeMs
  check('host: exactly one caption after stabilization', h.captions.length === 1, JSON.stringify(h.captions));
  check('host: speaker + text + stable are the final values',
    h.captions[0]?.speaker === 'Priya Nair'
    && h.captions[0]?.text === 'We should ship the CC lane.'
    && h.captions[0]?.stable === true, JSON.stringify(h.captions[0]));
  check('host: tMs is the injected epoch clock', h.captions[0]?.tMs === 1_000_000 + 1360);

  h.tick(500);
  check('host: a settled entry is not re-emitted by later scans', h.captions.length === 1);
  h.destroy();
}

// ── 2. Guest view (no items-renderer) + a new speaker finalizes the previous ──
{
  const rows = [captionRow('Priya Nair', 'first turn here')];
  const wrapper = guestWrapper(rows);
  const h = harness(e('body', {}, [wrapper]));
  h.scan();
  check('guest: nothing yet (still inside the stabilization window)', h.captions.length === 0);

  // A NEW entry appears at the tail: the previous one can never change again.
  const second = captionRow('Sven Olsen', 'second turn');
  wrapper.kids[0].append(second);
  h.tick(100);
  check('guest: the superseded entry is emitted immediately as stable',
    h.captions.length === 1 && h.captions[0].speaker === 'Priya Nair'
    && h.captions[0].text === 'first turn here' && h.captions[0].stable === true, JSON.stringify(h.captions));

  h.tick(1000);
  check('guest: the new speaker stabilizes as its own entry',
    h.captions.length === 2 && h.captions[1].speaker === 'Sven Olsen' && h.captions[1].text === 'second turn',
    JSON.stringify(h.captions));
  h.destroy();
}

// ── 3. Author unresolved: WITHHELD, and it says so ───────────────────────────
{
  const rows = [captionRow('', 'somebody said something')];
  const h = harness(e('body', {}, [hostWrapper(rows)]));
  h.tick(1000);
  check('empty author: no caption event is emitted', h.captions.length === 0, JSON.stringify(h.captions));
  const un = h.observations.filter((o) => o.type === 'caption-speaker-unresolved') as any[];
  check('empty author: caption-speaker-unresolved reason=author-empty',
    un.length >= 1 && un[0].reason === 'author-empty', JSON.stringify(h.observations));
  check('empty author: the observation carries NO caption text',
    !JSON.stringify(un[0]).includes('somebody said'), JSON.stringify(un[0]));
  h.destroy();
}
{
  // A machine-shaped author is refused by the SAME guard the tile path uses —
  // a stable attribute is not evidence its value is a person.
  const rows = [captionRow('closed-caption-text', 'machine token as an author')];
  const h = harness(e('body', {}, [hostWrapper(rows)]));
  h.tick(1000);
  check('machine-shaped author: withheld', h.captions.length === 0);
  check('machine-shaped author: reason=author-not-name-shaped',
    h.observations.some((o) => o.type === 'caption-speaker-unresolved' && (o as any).reason === 'author-not-name-shaped'),
    JSON.stringify(h.observations));
  h.destroy();
}

// ── 4. No caption renderer at all → captions-absent, ONCE, and nothing else ──
{
  const h = harness(e('body', {}, [ e('div', { 'data-tid': 'meeting-controls' }) ]), { absentAfterMs: 5000 });
  check('absent: silent before the detection window elapses',
    h.observations.length === 0, JSON.stringify(h.observations));
  h.tick(6000);
  const absent = h.observations.filter((o) => o.type === 'captions-absent') as any[];
  check('absent: one captions-absent after the window', absent.length === 1, JSON.stringify(h.observations));
  check('absent: reason=renderer-missing with per-candidate counts',
    absent[0].reason === 'renderer-missing' && Array.isArray(absent[0].candidates)
    && absent[0].candidates.every((c: any) => c.count === 0), JSON.stringify(absent[0]));
  h.tick(6000); h.tick(6000);
  check('absent: not repeated on later scans (once per state change)',
    h.observations.filter((o) => o.type === 'captions-absent').length === 1);
  check('absent: no caption events, ever', h.captions.length === 0);
  h.destroy();
}

// ── 5. Self-name captions are dropped ────────────────────────────────────────
{
  const rows = [captionRow('Vexa Notetaker', 'the bot should never caption itself')];
  const h = harness(e('body', {}, [hostWrapper(rows)]), { selfName: 'Vexa Notetaker' });
  h.tick(1000); h.tick(1000);
  check('self: no caption event for the bot\'s own attributed speech', h.captions.length === 0, JSON.stringify(h.captions));
  check('self: no unresolved observation either (it resolved fine — it is us)',
    !h.observations.some((o) => o.type === 'caption-speaker-unresolved'));
  h.destroy();
}

// ── 5b. A human name containing the bot name is not the bot ─────────────────
{
  const rows = [captionRow('Vexa Petrova', 'a real human keeps her caption attribution')];
  const h = harness(e('body', {}, [hostWrapper(rows)]), { selfName: 'Vexa' });
  h.tick(1000); h.tick(1000);
  check('caption self filtering is exact and preserves Vexa Petrova',
    h.captions.length === 1 && h.captions[0]?.speaker === 'Vexa Petrova', JSON.stringify(h.captions));
  h.destroy();
}

// ── 6. destroy() flushes an entry still refining, marked stable:false ────────
{
  const rows = [captionRow('Tariq B', 'mid sentence when the meeting end')];
  const h = harness(e('body', {}, [hostWrapper(rows)]));
  h.tick(100);
  check('tail: nothing emitted yet', h.captions.length === 0);
  h.destroy();
  check('tail: destroy flushes the pending entry', h.captions.length === 1, JSON.stringify(h.captions));
  check('tail: the flushed entry is marked stable:false',
    h.captions[0]?.stable === false && h.captions[0]?.speaker === 'Tariq B', JSON.stringify(h.captions[0]));
}

// ── 7. Flake class: lost mid-meeting, then recovered ────────────────────────
{
  // Captions live, one entry settled, then the renderer vanishes (captions turned
  // off, or the panel re-mounted) — and later comes back.
  const rows = [captionRow('Priya Nair', 'before the outage')];
  const body = e('body', {}, [hostWrapper(rows)]);
  const wrapperNode = body.kids[0];
  const h = harness(body);
  h.tick(1000);
  check('flake: a caption emitted while captions were live', h.captions.length === 1, JSON.stringify(h.captions));

  body.kids.length = 0;                     // the renderer disappears mid-meeting
  h.tick(250);
  const lost = h.observations.filter((o) => o.type === 'captions-lost') as any[];
  check('flake: captions-lost on the drop-out', lost.length === 1, JSON.stringify(h.observations));
  check('flake: captions-lost says how much it had delivered (was-working vs never-did)',
    lost[0].emitted === 1 && lost[0].reason === 'renderer-lost', JSON.stringify(lost[0]));
  h.tick(250); h.tick(250);
  check('flake: the loss is reported once, not once per poll',
    h.observations.filter((o) => o.type === 'captions-lost').length === 1);

  body.append(wrapperNode);                 // …and comes back
  rows.length = 0;
  wrapperNode.kids[0].kids[0].kids.length = 0;
  wrapperNode.kids[0].kids[0].append(captionRow('Sven Olsen', 'after the outage'));
  h.tick(250);
  const rec = h.observations.filter((o) => o.type === 'captions-recovered') as any[];
  check('flake: captions-recovered when the renderer reappears (automatic rescan)',
    rec.length === 1 && rec[0].losses === 1, JSON.stringify(h.observations));
  h.tick(1000);
  check('flake: caption capture resumes after the outage',
    h.captions.length === 2 && h.captions[1].speaker === 'Sven Olsen', JSON.stringify(h.captions));
  h.destroy();
}

// ── 8. A throwing consumer never breaks the watcher ─────────────────────────
{
  setDoc(e('body', {}, [hostWrapper([captionRow('Ana Ruiz', 'hello there')])]));
  let clock = 2_000_000;
  let threw = false;
  const cc = createTeamsCaptions({
    onCaption: () => { throw new Error('consumer blew up'); },
    onObservation: () => { throw new Error('observer blew up'); },
    now: () => clock,
  });
  try { clock += 2000; cc.scanNow(); cc.destroy(); } catch { threw = true; }
  check('a throwing onCaption/onObservation is swallowed (best-effort contract)', !threw);
}

if (failed) { console.error(`\n❌ teams-captions: ${failed} checks FAILED.`); process.exit(1); }
console.log('\n✅ teams-captions: 0.10 caption DOM (host + guest), stabilization dedup, withheld-unresolved, absent-once, self-filter, tail flush, lost→recovered across an outage.');

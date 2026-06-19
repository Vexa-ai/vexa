/**
 * gmeet-capture L2 (speakers) — the PURE glow→hint logic, no browser. Drives the
 * real createGmeetSpeakers against an in-memory DOM shim + a manual interval, and
 * pins the attribution rules: a newly-lit named non-self tile emits a SPEAKER_START
 * hint, going quiet emits SPEAKER_END, the self tile (data-self-name) never emits,
 * and junk names are filtered. The DOM scraping itself is live-validated; this
 * pins the hint-edge logic that has no business touching a page.
 * Run: npx tsx src/gmeet-speakers.test.ts   (the package's `npm test` chains both)
 */
import { createGmeetSpeakers } from './gmeet-speakers.js';

let failed = 0;
const check = (name: string, cond: boolean) => { console.log(`  ${cond ? '✅' : '❌'} ${name}`); if (!cond) failed++; };

// ── Minimal in-memory DOM shim (tag, .class, #id, [attr], [attr*="v"], comma
//    lists) + hasAttribute + CSS.escape — no jsdom dependency ──────────────────
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
  get textContent(): string { let s = this.ownText; for (const k of this.kids) s += k.textContent; return s; }
  getAttribute(n: string): string | null { return n in this.attrs ? this.attrs[n] : null; }
  hasAttribute(n: string): boolean { return n in this.attrs; }
  get classList() { const s = new Set((this.attrs['class'] || '').split(/\s+/).filter(Boolean)); return { contains: (c: string) => s.has(c) }; }
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
let intervalCb: (() => void) | null = null;
g.CSS = { escape: (s: string) => s };
g.setInterval = (cb: () => void) => { intervalCb = cb; return 1 as any; };
g.clearInterval = () => {};
const setDoc = (root: FakeEl) => { g.document = makeDocument(root); };
const tick = () => intervalCb?.();

// A participant tile: data-participant-id + a name span; `speaking` adds a known
// glow class; `self` marks it with Meet's data-self-name structural marker.
const tile = (id: string, name: string, opts: { speaking?: boolean; self?: boolean } = {}) => {
  const attrs: Record<string, string> = { 'data-participant-id': id };
  if (opts.speaking) attrs['class'] = 'Oaajhc';                 // a KNOWN_SPEAKING_CLASS
  if (opts.self) attrs['data-self-name'] = name;
  return e('div', attrs, [ t('span', name, { class: 'notranslate' }) ]);
};

// ── newly-lit named non-self tile → SPEAKER_START; quiet → SPEAKER_END ─────────
{
  const hints: { name: string; isEnd: boolean }[] = [];
  setDoc(e('body', {}, [ tile('p1', 'Alice', { speaking: true }), tile('p2', 'Bob') ]));
  const sp = createGmeetSpeakers({ pollMs: 10, onSpeaking: (name, isEnd) => hints.push({ name, isEnd }) });
  tick();                                            // Alice lit → START
  check('lit named tile emits a START hint', hints.some((h) => h.name === 'Alice' && !h.isEnd));
  check('only Alice is lit (Bob silent → no hint)', !hints.some((h) => h.name === 'Bob'));
  check('litNames() reports Alice', sp.litNames().includes('Alice'));
  setDoc(e('body', {}, [ tile('p1', 'Alice'), tile('p2', 'Bob') ]));   // Alice goes quiet
  tick();
  check('going quiet emits an END hint', hints.some((h) => h.name === 'Alice' && h.isEnd));
  check('litNames() now empty', sp.litNames().length === 0);
  sp.destroy();
}
{
  // The self tile (data-self-name) never emits a hint, even while glowing.
  const hints: { name: string }[] = [];
  setDoc(e('body', {}, [ tile('me', 'Host', { speaking: true, self: true }), tile('p2', 'Carol', { speaking: true }) ]));
  const sp = createGmeetSpeakers({ pollMs: 10, onSpeaking: (name) => hints.push({ name }) });
  tick();
  check('self tile never emits', !hints.some((h) => h.name === 'Host'));
  check('a non-self glowing tile still emits', hints.some((h) => h.name === 'Carol'));
  sp.destroy();
}
{
  // Junk names ("Google Participant (…", caption phrases) are filtered out.
  const hints: { name: string }[] = [];
  const junk = e('div', { 'data-participant-id': 'j1', class: 'Oaajhc' }, [ t('span', 'Google Participant (guest)', { class: 'notranslate' }) ]);
  setDoc(e('body', {}, [ junk ]));
  const sp = createGmeetSpeakers({ pollMs: 10, onSpeaking: (name) => hints.push({ name }) });
  tick();
  check('junk participant name is filtered (no hint)', hints.length === 0);
  sp.destroy();
}

if (failed) { console.error(`\n❌ gmeet-speakers: ${failed} checks FAILED.`); process.exit(1); }
console.log(`\n✅ gmeet-speakers: glow→START/END hint edges, self-tile suppression, junk-name filter pass. (DOM scraping is live-validated.)`);

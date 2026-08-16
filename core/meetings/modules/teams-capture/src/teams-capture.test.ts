/**
 * teams-capture L2 — the PURE parsing logic, no browser. Drives the real
 * createTeamsChat (sender/body extraction, group-header climb, aria fallback
 * "Name, 10:42 AM, …", trailing-timestamp strip) against an in-memory DOM shim,
 * and pins the exported selector arrays (the module's public WHO-signal surface).
 * The blue-square speaker detector is MutationObserver/RAF-driven → live-validated.
 * Run: npm test  or  npx tsx src/teams-capture.test.ts
 */
import {
  createTeamsChat,
  teamsNameFromStream,
  teamsParticipantSelectors,
  teamsNameSelectors,
  teamsParticipantIdSelectors,
  teamsMeetingContainerSelectors,
  plausibleNameFromLeaves,
} from './index.js';

let failed = 0;
const check = (name: string, cond: boolean) => { console.log(`  ${cond ? '✅' : '❌'} ${name}`); if (!cond) failed++; };

// ── Minimal in-memory DOM shim (tag, .class, #id, [attr], [attr="v"],
//    [attr*="v"], [attr^="v"], comma lists) — no jsdom dependency ──────────────
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
  getAttribute(n: string): string | null { return n in this.attrs ? this.attrs[n] : null; }
  get classList() { const s = new Set((this.attrs['class'] || '').split(/\s+/).filter(Boolean)); return { contains: (c: string) => s.has(c) }; }
  matches(sel: string): boolean { return compile(sel)(this); }
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
g.setInterval = g.window.setInterval; g.clearInterval = g.window.clearInterval;
const setDoc = (root: FakeEl) => { g.document = makeDocument(root); };

function firstChatMessage(root: FakeEl): { sender: string; text: string } | undefined {
  setDoc(root);
  const out: { sender: string; text: string }[] = [];
  const chat = createTeamsChat({ onMessage: (m) => out.push(m) });
  chat.destroy();
  return out[0];
}

// ── Exported selector surface (the bot re-exports these — keep them sane) ──────
check('participant selectors are a non-empty string[]', teamsParticipantSelectors.length > 0 && teamsParticipantSelectors.every((s) => typeof s === 'string'));
check('participant discovery starts from the exact voice outline and stable stream wrapper',
  teamsParticipantSelectors[0] === '[data-tid="voice-level-stream-outline"]'
    && teamsParticipantSelectors[1] === '[data-stream-type][data-tid]');
check('name selectors non-empty', teamsNameSelectors.length > 0);
check('participant-id selectors include [data-tid]', teamsParticipantIdSelectors.includes('[data-tid]'));
check('container selectors fall back to body', teamsMeetingContainerSelectors.includes('body'));

// ── plausibleNameFromLeaves: the rot-proof name path ──────────────────────────
// Captured from a live Teams meeting (2026-08): the roster tile carries NO data-tid,
// title or aria-label, and the name div's only marker is an obfuscated build-specific
// class. The previously hardcoded `___2u340f0` had rotted to `___12zni01`, which
// suppressed every speaker hint and collapsed the transcript to a single 'Speaker'.
{
  const tile = e('div', { class: '___1504rl1 f1euv43f ftuwxu6 f4d9j23', role: 'menuitem' }, [
    e('div', { class: '___12zni01 f1cmbuwj fv6wr3j fz5stix f1p9o1ba' }, [
      t('div', 'Priya Nair', { class: '___12zni01 f1cmbuwj fv6wr3j fz5stix f1p9o1ba' }),
    ]),
  ]);
  check('name: live 2026-08 roster tile resolves despite the rotted class',
    plausibleNameFromLeaves(tile as never) === 'Priya Nair');

  // A future obfuscated class must not regress it — structure, not the hash, is the key.
  const rotated = e('div', { class: '___zzzzzz9 whatever', role: 'menuitem' }, [
    t('div', 'Tariq Baig', { class: '___future0 abc' }),
  ]);
  check('name: survives a further class rotation',
    plausibleNameFromLeaves(rotated as never) === 'Tariq Baig');

  // Control affordances share the tile and must never win.
  const noisy = e('div', {}, [
    t('span', 'mic_off', {}),
    t('span', '10:42 AM', {}),
    t('span', '2', {}),
    t('div', 'Priya Nair', {}),
  ]);
  check('name: skips control labels, timestamps and bare digits',
    plausibleNameFromLeaves(noisy as never) === 'Priya Nair');

  // No resolvable name → empty, so emit() still suppresses rather than inventing a GUID.
  const empty = e('div', {}, [t('span', 'mic_off', {}), t('span', '', {})]);
  check('name: unresolvable stays empty (no hint is better than a wrong hint)',
    plausibleNameFromLeaves(empty as never) === '');

  // A wrapper's concatenated text must never win over its leaves.
  const nested = e('div', {}, [
    e('div', {}, [t('span', 'Priya Nair', {}), t('span', 'Presenting', {})]),
  ]);
  check('name: leaf wins over concatenated wrapper text',
    plausibleNameFromLeaves(nested as never) === 'Priya Nair');
}

// ── createTeamsChat extraction ────────────────────────────────────────────────
{
  // Explicit author + body via data-tid selectors.
  const msg = e('div', { 'data-tid': 'chat-pane-message' }, [
    t('div', 'Priya Nair', { 'data-tid': 'message-author-name' }),
    t('div', 'See the deck I shared', { 'data-tid': 'messageBodyContent' }),
  ]);
  const got = firstChatMessage(e('body', {}, [ e('div', { 'data-tid': 'chat-pane-list' }, [ msg ]) ]));
  check('chat: author from [data-tid=message-author-name]', got?.sender === 'Priya Nair');
  check('chat: body from [data-tid=messageBodyContent]', got?.text === 'See the deck I shared');
}
{
  // No author node → aria-label "Name, 10:42 AM, …" fallback + largest-leaf body.
  const msg = e('div', { 'data-mid': 'm1', 'aria-label': 'Sven Olsen, 10:42 AM, hello there' }, [ t('span', 'a much longer body line here') ]);
  const got = firstChatMessage(e('body', {}, [ e('div', { role: 'log' }, [ msg ]) ]));
  check('chat: sender from aria-label "Name, 10:42 AM"', got?.sender === 'Sven Olsen');
  check('chat: body via largest-leaf fallback', got?.text === 'a much longer body line here');
}
{
  // Sender grouped on an ancestor wrapper; trailing timestamp stripped.
  const msg = e('div', { 'data-tid': 'chat-pane-message' }, [ t('div', 'the actual message text', { 'data-tid': 'messageBodyContent' }) ]);
  const wrapper = e('div', {}, [ t('div', 'Tariq B 10:42 AM', { class: 'author-name' }), msg ]);
  const got = firstChatMessage(e('body', {}, [ e('div', { 'data-tid': 'chat-pane-list' }, [ wrapper ]) ]));
  check('chat: sender climbed from the group wrapper', got?.sender === 'Tariq B');
  check('chat: text intact', got?.text === 'the actual message text');
}

// ── Speaker name resolution: Teams' stable `data-tid` on the [data-stream-type] wrapper ──
// (the hashed-class name selectors rot every Teams release; this attribute pair is durable).
{
  const outline = () => e('div', { 'data-tid': 'voice-level-stream-outline' });
  // name sits on the [data-stream-type] wrapper that ancestors the voice outline
  const tile = e('div', { 'data-tid': 'video-tile' }, [
    e('div', { 'data-tid': 'Jane Doe', 'data-stream-type': 'Video' }, [ e('div', {}, [ outline() ]) ]),
  ]);
  check('teams name from data-tid on [data-stream-type]', teamsNameFromStream(tile as any) === 'Jane Doe');

  // anchors on the stream nearest the voice outline — NOT the first [data-stream-type] in DOM order
  const multi = e('div', {}, [
    e('div', { 'data-tid': 'Bob Stone', 'data-stream-type': 'Video' }, []),
    e('div', { 'data-tid': 'Jane Doe', 'data-stream-type': 'Video' }, [ outline() ]),
  ]);
  check('teams name anchors on the speaking stream, not DOM order', teamsNameFromStream(multi as any) === 'Jane Doe');

  // no stream wrapper → '' so the caller falls back to the legacy selectors
  check('teams name empty when no [data-stream-type] wrapper', teamsNameFromStream(e('div', {}, [ e('span', {}) ]) as any) === '');

  // A STABLE attribute is not evidence its value is a person. Control-label /
  // timer / machine-token shapes are refused on this path exactly as on every
  // other one: unknown beats a confident wrong name.
  for (const machine of ['video-stream-2', 'voice-level-stream-outline', 'videoTile', 'mic_off', '05:14', '12345']) {
    const rogue = e('div', {}, [
      e('div', { 'data-tid': machine, 'data-stream-type': 'Video' }, [ outline() ]),
    ]);
    check(`teams name rejects control-shaped data-tid "${machine}"`, teamsNameFromStream(rogue as any) === '');
  }
}

if (failed) { console.error(`\n❌ teams-capture: ${failed} checks FAILED.`); process.exit(1); }
console.log(`\n✅ teams-capture: chat extraction + the exported selector surface pass. (blue-square speaker detection is live-validated.)`);

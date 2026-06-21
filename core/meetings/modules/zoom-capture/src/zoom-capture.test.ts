/**
 * zoom-capture L2 — the PURE parsing/binding logic, no browser. Drives the real
 * createZoomChat (sender/text extraction, group-header climb, aria fallback,
 * trailing-timestamp strip) and createZoomSpeakers (active-speaker read + the
 * flicker-confirmation that a single transient poll must NOT emit a hint) against
 * an in-memory DOM shim + a manual setInterval. DOM capture itself is live-validated.
 * Run: npm test  or  npx tsx src/zoom-capture.test.ts
 */
import { createZoomChat } from './zoom-chat.js';
import { createZoomSpeakers } from './zoom-speakers.js';

let failed = 0;
const check = (name: string, cond: boolean) => { console.log(`  ${cond ? '✅' : '❌'} ${name}`); if (!cond) failed++; };

// ── Minimal in-memory DOM shim (no jsdom; supports tag, .class, #id, [attr],
//    [attr="v"], [attr*="v"], [attr^="v"], comma lists) ────────────────────────
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
  get textContent(): string { let t = this.ownText; for (const k of this.kids) t += k.textContent; return t; }
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

// Install the shim as the ambient document + a controllable interval clock.
const g = globalThis as any;
let intervalCb: (() => void) | null = null;
g.MutationObserver = class { observe() {} disconnect() {} };
g.window = { setInterval: (cb: () => void) => { intervalCb = cb; return 1 as any; }, clearInterval: () => {} };
g.setInterval = g.window.setInterval; g.clearInterval = g.window.clearInterval;
const setDoc = (root: FakeEl) => { g.document = makeDocument(root); };
const tickN = (n: number) => { for (let i = 0; i < n; i++) intervalCb?.(); };

// ── createZoomChat: extract sender + body from a Zoom-shaped message node ──────
// Collect the first message createZoomChat emits for a given DOM (array, not a
// nullable local, so tsc doesn't narrow the capture to `never`).
function firstChatMessage(root: FakeEl): { sender: string; text: string } | undefined {
  setDoc(root);
  const out: { sender: string; text: string }[] = [];
  const chat = createZoomChat({ onMessage: (m) => out.push(m) });
  chat.destroy();
  return out[0];
}
{
  // Group header carries the sender; the message node carries the text.
  const msg = e('div', { id: 'chat-message-7' }, [ t('div', 'Hello team', { class: 'chat-message-text' }) ]);
  const group = e('div', {}, [ t('div', 'Barbara W.', { class: 'chat-message-text__user-name' }), msg ]);
  const got = firstChatMessage(e('body', {}, [ e('div', { 'aria-label': 'Chat Message List' }, [ group ]) ]));
  check('chat: body extracted from the message node', got?.text === 'Hello team');
  check('chat: sender climbed from the group header', got?.sender === 'Barbara W.');
}
{
  // No explicit sender node → aria-label "X said to Everyone …" fallback.
  const msg = e('div', { id: 'chat-message-1', 'aria-label': 'Carlos said to Everyone at 10:42 AM: Hi' }, [ t('span', 'Hi there everyone') ]);
  const got = firstChatMessage(e('body', {}, [ e('div', { id: 'chat-list-content' }, [ msg ]) ]));
  check('chat: sender from aria-label "X said to Everyone"', got?.sender === 'Carlos');
  check('chat: body via largest-leaf fallback', got?.text === 'Hi there everyone');
}
{
  // Trailing timestamp on the sender row is stripped.
  const msg = e('div', { id: 'chat-message-2' }, [ t('div', 'Dana 10:42 AM', { class: 'user-name' }), t('div', 'meeting note', { class: 'message-text' }) ]);
  const got = firstChatMessage(e('body', {}, [ e('div', { id: 'chat-list-content' }, [ msg ]) ]));
  check('chat: trailing "10:42 AM" stripped from sender', got?.sender === 'Dana');
}

// ── createZoomSpeakers: active-speaker read + flicker confirmation ─────────────
const speakerTile = (name: string) =>
  e('div', { class: 'speaker-active-container__video-frame' }, [
    e('div', { class: 'video-avatar__avatar-footer' }, [ t('span', name) ]),
  ]);
{
  // Alice is the lit speaker; the heartbeat re-asserts. pollMs=10 so CONFIRM_POLLS
  // (2) is a couple of manual ticks.
  setDoc(e('body', {}, [ speakerTile('Alice') ]));
  const changes: (string | null)[] = [];
  const sp = createZoomSpeakers({ pollMs: 10, onSpeakerChange: (n) => changes.push(n) });
  // initial tick() already ran on create; Alice should commit after CONFIRM_POLLS.
  tickN(2);
  check('speaker: active speaker read from the lit tile', sp.getActiveSpeaker() === 'Alice');
  check('speaker: emitted the Alice change exactly once so far', changes.filter((c) => c === 'Alice').length === 1);
  sp.destroy();
}
{
  // A single-poll flicker to "Bob" must NOT emit (needs CONFIRM_POLLS=2 holds).
  const root = e('body', {}, [ speakerTile('Alice') ]);
  setDoc(root);
  const changes: (string | null)[] = [];
  const sp = createZoomSpeakers({ pollMs: 10, onSpeakerChange: (n) => changes.push(n) });
  tickN(2);                                  // commit Alice
  setDoc(e('body', {}, [ speakerTile('Bob') ]));
  tickN(1);                                  // one flicker poll on Bob
  setDoc(e('body', {}, [ speakerTile('Alice') ]));   // back to Alice before confirm
  tickN(1);
  check('speaker: a single flicker poll never emits Bob', !changes.includes('Bob'));
  check('speaker: still Alice after the flicker', sp.getActiveSpeaker() === 'Alice');
  sp.destroy();
}
{
  // selfName is never reported as the remote speaker.
  setDoc(e('body', {}, [ speakerTile('Me Myself') ]));
  const sp = createZoomSpeakers({ pollMs: 10, selfName: 'Me Myself', onSpeakerChange: () => {} });
  tickN(3);
  check('speaker: selfName tile is suppressed (no active speaker)', sp.getActiveSpeaker() === null);
  sp.destroy();
}

if (failed) { console.error(`\n❌ zoom-capture: ${failed} checks FAILED.`); process.exit(1); }
console.log(`\n✅ zoom-capture: chat sender/body extraction + active-speaker flicker confirmation pass. (DOM capture is live-validated.)`);

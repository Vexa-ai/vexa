/**
 * gmeet-capture L2 (chat) — the chat reader + sender against a DOM fixture of the
 * Meet chat panel, no browser. Drives the real createGmeetChat / sendGmeetChatMessage
 * over an in-memory DOM shim + a manual interval + a manual MutationObserver, and pins:
 * history on screen when the panel opens is primed (not emitted), a new message from
 * another participant emits exactly once (repeat mutations and a re-render under the
 * same data-message-id do not re-emit), a run of messages under one sender header is
 * attributed to that sender, the bot's own "You" messages and the text it just sent
 * are skipped, a closed panel is opened, and the sender fills + sends through the
 * composer and counts the send only once the line shows in the list (no composer → no_composer,
 * never shown → not_landed). Hidden fields and Meet's reCAPTCHA response textarea are never the
 * composer. The live Meet DOM is validated live.
 * Run: npx tsx src/gmeet-chat.test.ts   (the package's `npm test` chains it)
 */
import { createGmeetChat, sendGmeetChatMessage, openGmeetChatPanel, findGmeetChatComposer, type GmeetChatMessage } from './gmeet-chat.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string) => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

// ── Minimal in-memory DOM shim: tag, .class, #id, [attr], [attr="v"], [attr*="v" i],
//    compound selectors, comma lists; mutable children; click/focus/dispatchEvent recorded ──
type Cond = (el: FakeEl) => boolean;
function simple(sel: string): Cond {
  const attr = sel.match(/^\[([a-zA-Z0-9_-]+)(?:([*^]?=)"([^"]*)"(\s+i)?)?\]$/);
  if (attr) {
    const [, name, op, raw, ci] = attr;
    return (el) => {
      const got = el.getAttribute(name);
      if (got == null) return false;
      if (!op) return true;
      const v = ci ? got.toLowerCase() : got;
      const want = ci ? raw.toLowerCase() : raw;
      if (op === '=') return v === want;
      if (op === '*=') return v.includes(want);
      if (op === '^=') return v.startsWith(want);
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
  const parts = sel.trim().match(/(\[[^\]]*\]|[.#]?[a-zA-Z0-9_*-]+)/g) || [sel];
  const cs = parts.map(simple);
  return (el) => cs.every((c) => c(el));
}
function compile(selector: string): Cond {
  const gs = selector.split(/,(?![^[]*\])/).map((s) => compound(s));
  return (el) => gs.some((g) => g(el));
}

class FakeEl {
  tag: string; attrs: Record<string, string>; ownText: string; kids: FakeEl[] = []; parentElement: FakeEl | null = null;
  value = ''; clicks = 0; focused = false; events: Array<{ type: string; key?: string }> = [];
  constructor(tag: string, attrs: Record<string, string> = {}, kids: FakeEl[] = [], text = '') {
    this.tag = tag.toLowerCase(); this.attrs = attrs; this.ownText = text;
    for (const k of kids) this.append(k);
  }
  append(k: FakeEl): FakeEl { k.parentElement = this; this.kids.push(k); return k; }
  remove(): void { if (this.parentElement) this.parentElement.kids = this.parentElement.kids.filter((k) => k !== this); this.parentElement = null; }
  get textContent(): string { let s = this.ownText; for (const k of this.kids) s += k.textContent; return s; }
  set textContent(v: string) { this.ownText = v; this.kids = []; }
  getAttribute(n: string): string | null { return n in this.attrs ? this.attrs[n] : null; }
  setAttribute(n: string, v: string): void { this.attrs[n] = v; }
  hasAttribute(n: string): boolean { return n in this.attrs; }
  get classList() { const s = new Set((this.attrs['class'] || '').split(/\s+/).filter(Boolean)); return { contains: (c: string) => s.has(c) }; }
  matches(sel: string): boolean { return compile(sel)(this); }
  private desc(): FakeEl[] { const out: FakeEl[] = []; const w = (e: FakeEl) => { for (const k of e.kids) { out.push(k); w(k); } }; w(this); return out; }
  querySelector(sel: string): FakeEl | null { const c = compile(sel); return this.desc().find(c) || null; }
  querySelectorAll(sel: string): FakeEl[] { return this.desc().filter(compile(sel)); }
  click(): void { this.clicks++; onClick?.(this); }
  focus(): void { this.focused = true; }
  dispatchEvent(ev: { type: string; key?: string }): boolean { this.events.push({ type: ev.type, key: ev.key }); return true; }
}
let onClick: ((el: FakeEl) => void) | null = null;
const e = (tag: string, attrs: Record<string, string> = {}, kids: FakeEl[] = []) => new FakeEl(tag, attrs, kids);
const t = (tag: string, text: string, attrs: Record<string, string> = {}) => new FakeEl(tag, attrs, [], text);

const g = globalThis as any;
let intervalCb: (() => void) | null = null;
g.setInterval = (cb: () => void) => { intervalCb = cb; return 1 as any; };
g.clearInterval = () => {};
const observers: Array<() => void> = [];
g.MutationObserver = class { cb: () => void; constructor(cb: () => void) { this.cb = cb; } observe() { observers.push(this.cb); } disconnect() {} };
const setDoc = (root: FakeEl) => {
  const all = () => [root, ...root.querySelectorAll('*')];
  g.document = { body: root, querySelector: (s: string) => all().find(compile(s)) || null, querySelectorAll: (s: string) => all().filter(compile(s)) };
};
const tick = () => intervalCb?.();
const mutate = () => observers.forEach((cb) => cb());

// ── Meet chat-panel fixture: a message GROUP is one sender header + a run of messages,
//    each `[data-message-id]` holding its text in `[jsname="dTKtvb"]` ──
const message = (id: string, text: string) => e('div', { 'data-message-id': id }, [t('div', text, { jsname: 'dTKtvb' })]);
const group = (sender: string, msgs: Array<[string, string]>) => {
  const list = e('div', { class: 'beTDc' }, msgs.map(([id, text]) => message(id, text)));
  const g = e('div', { class: 'Ss4fHf' }, [e('div', { class: 'HNucUd' }, [t('div', sender, { class: 'poVWob' }), t('div', '10:42 AM', { class: 'MuzmKe' })]), list]);
  return { el: g, list };
};
const composerArea = () => {
  const textarea = e('textarea', { 'aria-label': 'Send a message' });
  const send = e('button', { 'aria-label': 'Send a message' });
  return { area: e('div', { class: 'composer' }, [e('div', {}, [textarea]), send]), textarea, send };
};
const toolbar = (pressed: boolean) => e('div', {}, [
  e('button', { 'aria-label': 'Send a reaction' }),
  e('button', { 'aria-label': 'Chat with everyone', 'aria-pressed': String(pressed) }),
]);

// ── 1) Reader: prime, emit-once, grouped attribution, self + just-sent skipped ──
{
  const got: GmeetChatMessage[] = [];
  const history = group('Alice', [['m1', 'from before the bot joined']]);
  const list = e('div', { jsname: 'xySENc' }, [history.el]);
  const { area } = composerArea();
  setDoc(e('body', {}, [toolbar(true), e('aside', {}, [list, area])]));
  const chat = createGmeetChat({ onMessage: (m) => got.push(m) });

  check('history on screen when the panel opens is primed, not emitted', got.length === 0, JSON.stringify(got));
  check('the reader reports itself primed on the matched container',
    chat.getState().primed && chat.getState().matchedContainer === '[jsname="xySENc"]', JSON.stringify(chat.getState()));

  const bob = group('Bob', [['m2', 'hello bot']]);
  list.append(bob.el);
  mutate();
  check('a new message from another participant emits once',
    got.length === 1 && got[0].sender === 'Bob' && got[0].text === 'hello bot', JSON.stringify(got));
  mutate(); tick();
  check('a repeated mutation / poll does not re-emit it', got.length === 1, JSON.stringify(got));

  bob.list.append(message('m3', 'second line'));
  mutate();
  check('a second line under the same sender header is attributed to that sender',
    got.length === 2 && got[1].sender === 'Bob' && got[1].text === 'second line', JSON.stringify(got));

  // Meet re-renders the list (panel reopened): same ids, fresh nodes → nothing new.
  bob.el.remove();
  list.append(group('Bob', [['m2', 'hello bot'], ['m3', 'second line']]).el);
  mutate();
  check('a re-render under the same data-message-id does not re-emit', got.length === 2, JSON.stringify(got));

  // The id is the identity: the same words sent twice are two messages.
  list.append(group('Bob', [['m5', 'ok']]).el);
  mutate();
  list.append(group('Bob', [['m6', 'ok']]).el);
  mutate();
  check('the same text sent twice (distinct data-message-id) emits twice', got.length === 4, JSON.stringify(got));

  list.append(group('You', [['m4', 'my own message']]).el);
  mutate();
  check('the bot\'s own "You" message is skipped', got.length === 4, JSON.stringify(got));

  chat.destroy();
}

// ── 2) The text the bot just sent is never echoed back, even under a non-"You" label ──
{
  const got: GmeetChatMessage[] = [];
  const list = e('div', { jsname: 'xySENc' }, []);
  const { area, send } = composerArea();
  setDoc(e('body', {}, [toolbar(true), e('aside', {}, [list, area])]));
  const chat = createGmeetChat({ onMessage: (m) => got.push(m) });
  // Meet renders the sent line (under a label that is not "You") when the send button is clicked.
  onClick = (el) => { if (el === send) { list.append(group('Vexa Notetaker', [['s1', 'auto reply']]).el); mutate(); } };
  const ok = await sendGmeetChatMessage('auto reply');
  onClick = null;
  check('the just-sent text is skipped by the reader', ok.ok && got.length === 0, `${JSON.stringify(ok)} ${JSON.stringify(got)}`);
  list.append(group('Carol', [['c1', 'auto reply']]).el);
  mutate();
  check('the recent-sent entry is consumed — the same words from a person later still emit',
    got.length === 1 && got[0].sender === 'Carol', JSON.stringify(got));
  chat.destroy();

  const named: GmeetChatMessage[] = [];
  const list2 = e('div', { jsname: 'xySENc' }, []);
  setDoc(e('body', {}, [toolbar(true), e('aside', {}, [list2, composerArea().area])]));
  const chat2 = createGmeetChat({ selfName: 'Vexa Notetaker', onMessage: (m) => named.push(m) });
  list2.append(group('Vexa Notetaker', [['n1', 'typed by hand into the bot']]).el);
  mutate();
  check('a message under the bot\'s own display name (selfName) is skipped', named.length === 0, JSON.stringify(named));
  chat2.destroy();
}

// ── 2b) Live Meet renders the bot's OWN line with no sender header (right-aligned, time only), and
//    may render it more than once — a pending node, then the confirmed one under a new
//    data-message-id. Every render of it is the bot's own; a person typing the same words is not. ──
{
  const got: GmeetChatMessage[] = [];
  const list = e('div', { jsname: 'xySENc' }, []);
  const { area, send, textarea } = composerArea();
  const ownLine = (id: string, text: string) => e('div', { class: 'Ss4fHf' }, [t('div', '9:47 PM', { class: 'MuzmKe' }), e('div', { class: 'beTDc' }, [message(id, text)])]);
  setDoc(e('body', {}, [toolbar(true), e('aside', {}, [list, area])]));
  const chat = createGmeetChat({ selfName: 'Vexa B', onMessage: (m) => got.push(m) });
  onClick = (el) => { if (el === send) { const pending = ownLine('tmp-1', textarea.value); list.append(pending); mutate(); pending.remove(); list.append(ownLine('srv-9', textarea.value)); mutate(); } };
  const ok = await sendGmeetChatMessage('car22d-B-plain-run1');
  onClick = null;
  tick();
  check('the bot\'s own unlabelled line, rendered twice under two ids, is never emitted', ok.ok && got.length === 0, `${JSON.stringify(ok)} ${JSON.stringify(got)}`);
  list.append(group('Carol', [['c9', 'car22d-B-plain-run1']]).el);
  mutate();
  check('a person typing the same words afterwards is still emitted', got.length === 1 && got[0].sender === 'Carol', JSON.stringify(got));
  chat.destroy();
}

// ── 3) A closed panel is opened; an empty panel primes without swallowing the first message ──
{
  const got: GmeetChatMessage[] = [];
  const bar = toolbar(false);
  const toggle = bar.querySelector('button[aria-label*="Chat with everyone" i]')!;
  const aside = e('aside', {}, []);
  const list = e('div', { jsname: 'xySENc' }, []);
  onClick = (el) => { if (el === toggle) { el.setAttribute('aria-pressed', 'true'); aside.append(list); aside.append(composerArea().area); } };
  setDoc(e('body', {}, [bar, aside]));
  const chat = createGmeetChat({ onMessage: (m) => got.push(m) });
  check('the reader clicks the "Chat with everyone" toggle when the panel is closed', toggle.clicks === 1, `clicks=${toggle.clicks}`);
  tick();
  check('the opened (empty) panel primes on the next poll', chat.getState().primed && chat.getState().panelOpen, JSON.stringify(chat.getState()));
  list.append(group('Dana', [['d1', 'first message after open']]).el);
  mutate();
  check('the first message into an empty, primed panel is emitted', got.length === 1 && got[0].sender === 'Dana', JSON.stringify(got));
  check('openGmeetChatPanel does not click a toggle that is already pressed', openGmeetChatPanel() && toggle.clicks === 1, `clicks=${toggle.clicks}`);
  onClick = null;
  chat.destroy();
}

// ── 4) Sender: no_composer with none; ok once the line lands; not_landed when it never shows ──
{
  setDoc(e('body', {}, [e('div', {}, [])]));
  const none = await sendGmeetChatMessage('nobody hears this');
  check('send fails no_composer when there is no composer and no chat toggle', !none.ok && none.reason === 'no_composer', JSON.stringify(none));

  const bar = toolbar(true);
  const reaction = bar.querySelector('button[aria-label*="Send a reaction" i]')!;
  const { area, textarea, send } = composerArea();
  const list = e('div', { jsname: 'xySENc' }, []);
  setDoc(e('body', {}, [bar, e('aside', {}, [list, area])]));
  // Meet renders the composer's text into the list when its send button is clicked.
  onClick = (el) => { if (el === send) list.append(group('You', [[`r${send.clicks}`, textarea.value]]).el); };
  const ok = await sendGmeetChatMessage('hello from the agent');
  check('send is ok once the sent line shows in the message list', ok.ok, JSON.stringify(ok));
  check('the composer value is set and focused', textarea.value === 'hello from the agent' && textarea.focused, `value=${textarea.value}`);
  check('an input event announces the value', textarea.events.some((ev) => ev.type === 'input'), JSON.stringify(textarea.events));
  check('the send button beside the composer is clicked', send.clicks === 1, `clicks=${send.clicks}`);
  check('the unrelated "Send a reaction" control is not clicked', reaction.clicks === 0, `clicks=${reaction.clicks}`);

  // The same words again: the earlier line is already on screen, so only a NEW one counts.
  onClick = null;
  const repeat = await sendGmeetChatMessage('hello from the agent', { landWaitMs: 50 });
  check('a send whose line never shows is not_landed — an earlier identical line does not count',
    !repeat.ok && repeat.reason === 'not_landed' && send.clicks === 2, JSON.stringify(repeat));

  const disabled = composerArea();
  disabled.send.setAttribute('disabled', '');
  setDoc(e('body', {}, [e('aside', {}, [disabled.area])]));
  await sendGmeetChatMessage('enter fallback', { landWaitMs: 10 });
  check('a disabled send button falls back to an Enter keydown on the composer',
    disabled.send.clicks === 0 && disabled.textarea.events.some((ev) => ev.type === 'keydown' && ev.key === 'Enter'),
    JSON.stringify(disabled.textarea.events));
}

// ── 5) Meet's in-call page as seen live: reCAPTCHA's response textarea sits next to a "Send
//    feedback" control, and the chat panel is closed. It is never the composer: the toggle is
//    clicked, and a send that cannot reach a visible composer fails without touching it. ──
{
  const recaptcha = e('textarea', { id: 'g-recaptcha-response-100000', name: 'g-recaptcha-response', class: 'g-recaptcha-response' });
  const feedback = e('button', { 'aria-label': 'Send feedback to Google' });
  const bar = toolbar(false);
  const toggle = bar.querySelector('button[aria-label*="Chat with everyone" i]')!;
  setDoc(e('body', {}, [bar, e('div', {}, [e('div', {}, [e('div', {}, [recaptcha])]), feedback])]));
  check('closed panel: the reCAPTCHA textarea (the only text field) is not the composer', findGmeetChatComposer() === null,
    JSON.stringify(findGmeetChatComposer()?.via));
  check('closed panel: openGmeetChatPanel clicks the chat toggle', openGmeetChatPanel() && toggle.clicks === 1, `clicks=${toggle.clicks}`);
  const res = await sendGmeetChatMessage('into the void', { waitMs: 30 });
  check('closed panel that never opens: send fails no_composer, reCAPTCHA field and feedback button untouched',
    !res.ok && res.reason === 'no_composer' && recaptcha.value === '' && feedback.clicks === 0,
    `${JSON.stringify(res)} value=${recaptcha.value} feedback=${feedback.clicks}`);

  // Open panel: a hidden field (aria-hidden ancestor, `hidden`) beside the real composer is skipped.
  const shadow = e('textarea', {});
  const { area, textarea } = composerArea();
  setDoc(e('body', {}, [toolbar(true), e('div', { 'aria-hidden': 'true' }, [shadow]), e('textarea', { hidden: '' }),
    e('aside', {}, [e('div', { jsname: 'xySENc' }, []), area]), e('div', {}, [recaptcha])]));
  const hit = findGmeetChatComposer();
  check('open panel: the composer is the visible field of the panel, never a hidden one or reCAPTCHA', hit?.el === textarea, hit?.via);
}

console.log(failed ? `\n❌ ${failed} check(s) failed` : '\n✅ gmeet-chat: all checks passed');
process.exit(failed ? 1 : 0);

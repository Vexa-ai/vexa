/**
 * Google Meet chat wiring — the page→Node boundary test, both directions.
 *
 * Drives the REAL page-side capture bundle (dist/browser-utils.global.js — the exact file the bot
 * injects via addInitScript) and the REAL startCaptureBridge / createChatSender against a fake
 * Playwright Page whose exposeFunction/evaluate run in-process over a DOM fixture of the Meet
 * chat panel:
 *   1. READ  — a participant's new chat line crosses __vexaChatMessage to the bridge's onChat
 *      sink with sender + text; history on screen at start is not delivered.
 *   2. WRITE — createChatSender on google_meet fills the composer and clicks send: through the
 *      bundle's sendGmeetChatMessage when the page has no Playwright locator, and with trusted
 *      Playwright input on the composer/send marks prepareGmeetChatSend sets when it has one; the
 *      line the bot just sent, once rendered, is NOT delivered back to onChat either way (the
 *      bundle's senders and reader share one recent-sent set).
 *   3. jitsi routes to sendJitsiChatMessage (the conference's own API); a platform with no chat
 *      sender reports false without touching the page.
 * RED at any base where the bundle lacks createGmeetChat or the gmeet branch of the bridge does
 * not start it: zero chat arrivals.
 * Run: npx tsx src/gmeet-chat-wiring.test.ts
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { startCaptureBridge, createChatSender } from './capture-bridge.js';
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

// ── Minimal DOM shim: compound selectors with [attr], [attr="v"], [attr*="v" i], .class, tag ──
type Cond = (el: El) => boolean;
const simple = (sel: string): Cond => {
  const a = sel.match(/^\[([a-zA-Z0-9_-]+)(?:([*^]?=)"([^"]*)"(\s+i)?)?\]$/);
  if (a) {
    const [, name, op, raw, ci] = a;
    return (el) => {
      const got = el.getAttribute(name);
      if (got == null) return false;
      if (!op) return true;
      const v = ci ? got.toLowerCase() : got, want = ci ? raw.toLowerCase() : raw;
      return op === '=' ? v === want : op === '*=' ? v.includes(want) : v.startsWith(want);
    };
  }
  if (sel.startsWith('.')) return (el) => (el.attrs['class'] || '').split(/\s+/).includes(sel.slice(1));
  if (sel === '*') return () => true;
  return (el) => el.tag === sel.toLowerCase();
};
const compile = (selector: string): Cond => {
  const groups = selector.split(/,(?![^[]*\])/).map((s) => (s.trim().match(/(\[[^\]]*\]|[.#]?[a-zA-Z0-9_*-]+)/g) || []).map(simple));
  return (el) => groups.some((cs) => cs.every((c) => c(el)));
};
class El {
  kids: El[] = []; parentElement: El | null = null; value = ''; clicks = 0;
  constructor(public tag: string, public attrs: Record<string, string> = {}, kids: El[] = [], public ownText = '') { kids.forEach((k) => this.append(k)); }
  append(k: El): El { k.parentElement = this; this.kids.push(k); return k; }
  get textContent(): string { return this.ownText + this.kids.map((k) => k.textContent).join(''); }
  set textContent(v: string) { this.ownText = v; this.kids = []; }
  getAttribute(n: string): string | null { return n in this.attrs ? this.attrs[n] : null; }
  setAttribute(n: string, v: string): void { this.attrs[n] = v; }
  removeAttribute(n: string): void { delete this.attrs[n]; }
  hasAttribute(n: string): boolean { return n in this.attrs; }
  matches(sel: string): boolean { return compile(sel)(this); }
  all(): El[] { const out: El[] = []; const w = (e: El) => e.kids.forEach((k) => { out.push(k); w(k); }); w(this); return out; }
  querySelector(sel: string): El | null { return this.all().find(compile(sel)) || null; }
  querySelectorAll(sel: string): El[] { return this.all().filter(compile(sel)); }
  click(): void { this.clicks++; }
  focus(): void { /* */ }
  dispatchEvent(): boolean { return true; }
}
const el = (tag: string, attrs: Record<string, string> = {}, kids: El[] = []) => new El(tag, attrs, kids);
const txt = (tag: string, text: string, attrs: Record<string, string> = {}) => new El(tag, attrs, [], text);
const group = (sender: string, id: string, text: string) => el('div', {}, [
  el('div', {}, [txt('div', sender, { class: 'poVWob' })]),
  el('div', {}, [el('div', { 'data-message-id': id }, [txt('div', text, { jsname: 'dTKtvb' })])]),
]);

const list = el('div', { jsname: 'xySENc' }, [group('Alice', 'h1', 'said before the bot joined')]);
const composer = el('textarea', { 'aria-label': 'Send a message' });
const sendBtn = el('button', { 'aria-label': 'Send a message' });
const root = el('body', {}, [
  el('button', { 'aria-label': 'Chat with everyone', 'aria-pressed': 'true' }),
  el('aside', {}, [list, el('div', {}, [el('div', {}, [composer]), sendBtn])]),
]);

// ── Page-context shims on the REAL globalThis (the fake page.evaluate runs in-process) ──
const g = globalThis as unknown as Record<string, any>;
g.document = { body: root, querySelector: (s: string) => root.querySelector(s), querySelectorAll: (s: string) => root.querySelectorAll(s) };
const intervals: Array<() => void> = [];
g.setInterval = (cb: () => void) => { intervals.push(cb); return intervals.length; };
g.clearInterval = () => { /* controlled clock */ };
const observers: Array<() => void> = [];
g.MutationObserver = class { constructor(private cb: () => void) {} observe() { observers.push(this.cb); } disconnect() { /* */ } };
g.window = g;
// The gmeet AUDIO capture is not under test here — mark it started so the bridge skips it.
g.__vexaGmeetCapture = { stop() { /* */ } };
const tick = () => intervals.forEach((cb) => cb());
const mutate = () => observers.forEach((cb) => cb());

new Function(readFileSync(BUNDLE, 'utf8'))();
const utils = g.VexaBrowserUtils as Record<string, unknown> | undefined;
check('bundle: VexaBrowserUtils exports createGmeetChat + sendGmeetChatMessage',
  typeof utils?.createGmeetChat === 'function' && typeof utils?.sendGmeetChatMessage === 'function',
  `keys: ${Object.keys(utils ?? {}).join(',')}`);

const page = {
  async exposeFunction(name: string, fn: unknown): Promise<void> { g[name] = fn; },
  async evaluate(fn: (arg: never) => unknown, arg?: unknown): Promise<unknown> { return fn(arg as never); },
} as never;
const pipeline: BotPipeline = {
  async start() { /* */ }, async stop() { /* */ }, feedAudio() { /* */ }, feedMixedAudio() { /* */ }, recordHint() { /* */ },
};
const inv = (platform: string) => ({ platform, botName: 'Vexa Bot', connectionId: 'test' }) as unknown as Invocation;

const chats: Array<{ sender: string; text: string }> = [];
const stop = await startCaptureBridge(page, inv('google_meet'), pipeline, undefined, (sender, text) => chats.push({ sender, text }));
check('the gmeet branch started the chat reader from the bundle', !!g.__vexaGmeetChat);
check('history on screen at start is not delivered', chats.length === 0, JSON.stringify(chats));

// 1. READ
list.append(group('Bob', 'b1', 'can the bot hear me?'));
mutate();
check('READ: a participant line crosses __vexaChatMessage to onChat with sender + text',
  chats.length === 1 && chats[0].sender === 'Bob' && chats[0].text === 'can the bot hear me?', JSON.stringify(chats));

// 2. WRITE
const sender = createChatSender(page, inv('google_meet'));
const ok = await sender.send('yes, loud and clear');
check('WRITE: google_meet send fills the composer and clicks send', ok && composer.value === 'yes, loud and clear' && sendBtn.clicks === 1,
  `ok=${ok} value=${composer.value} clicks=${sendBtn.clicks}`);
// Rendered under a label other than the bot's name, so only the shared recent-sent set can skip it.
list.append(group('Notetaker', 'v1', 'yes, loud and clear'));
mutate(); tick();
check('WRITE: the line the bot just sent is not delivered back to onChat', chats.length === 1, JSON.stringify(chats));

// 2b. WRITE through trusted input: a Playwright page fills the composer prepareGmeetChatSend marked
// and clicks the marked send button (the fake locator resolves the mark against the fixture).
const trustedOps: string[] = [];
const trustedPage = {
  ...(page as object),
  locator(sel: string) {
    const target = () => root.querySelector(sel);
    const handle = {
      async fill(t: string) { trustedOps.push(`fill ${sel}`); const el = target(); if (!el) throw new Error(`no ${sel}`); el.value = t; },
      async isEnabled() { return !!target(); },
      async click() { trustedOps.push(`click ${sel}`); const el = target(); if (!el) throw new Error(`no ${sel}`); el.click(); },
      async press(k: string) { trustedOps.push(`press ${k}`); },
    };
    return { first: () => handle };
  },
} as never;
const tok = await createChatSender(trustedPage, inv('google_meet')).send('sent with trusted input');
check('WRITE (trusted): Playwright fills the marked composer and clicks the marked send button',
  tok && composer.value === 'sent with trusted input' && sendBtn.clicks === 2
    && JSON.stringify(trustedOps) === JSON.stringify(['fill [data-vexa-chat-composer]', 'click [data-vexa-chat-send]']),
  `ok=${tok} value=${composer.value} clicks=${sendBtn.clicks} ops=${JSON.stringify(trustedOps)}`);
list.append(group('Notetaker', 'v2', 'sent with trusted input'));
mutate(); tick();
check('WRITE (trusted): the trusted line is not delivered back to onChat', chats.length === 1, JSON.stringify(chats));

// 3. jitsi + unsupported platforms
const jitsiSent: string[] = [];
g.APP = { conference: { _room: { sendTextMessage: (t: string) => jitsiSent.push(t) } } };
const jok = await createChatSender(page, inv('jitsi')).send('hi jitsi');
check('jitsi chat_send goes through sendJitsiChatMessage (the conference API)', jok && JSON.stringify(jitsiSent) === '["hi jitsi"]', JSON.stringify(jitsiSent));
let touched = false;
const spyPage = { async evaluate() { touched = true; return true; } } as never;
const zok = await createChatSender(spyPage, inv('zoom')).send('hi zoom');
check('a platform with no chat sender reports false without touching the page', zok === false && !touched);

await stop();
check('teardown destroys the chat reader', g.__vexaGmeetChat === null);

console.log(failed ? `\n❌ ${failed} check(s) failed` : '\n✅ gmeet-chat-wiring: all checks passed');
process.exit(failed ? 1 : 0);

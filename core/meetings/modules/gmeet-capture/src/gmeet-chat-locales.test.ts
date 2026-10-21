/**
 * gmeet-capture L2 (chat, languages) — the chat reader + sender over the Meet chat-panel fixtures in
 * several UI languages (src/fixtures/meet-chat-<lang>.html), parsed by a real DOM (jsdom). Pins that
 * nothing depends on an English label:
 *   • the English-only selectors the reader used to rely on match NOTHING in the nl / de / xx
 *     fixtures (the red this test guards against);
 *   • in every language the toggle, message list, composer and send button are found, and by the
 *     layer expected for that fixture (attribute, icon, structure);
 *   • history is primed, a new participant line is emitted once with its sender, the localised
 *     "You" line and the bot's own just-sent line are skipped;
 *   • the in-page sender fills the composer and clicks the row's send button (never the toolbar's
 *     reaction button); prepareGmeetChatSend marks the same two elements for trusted input; a closed
 *     panel is opened through the language-free toggle.
 * The fixtures are constructed, not live captures (see src/fixtures/README.md).
 * Run: npx tsx src/gmeet-chat-locales.test.ts   (the package's `npm test` chains it)
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import {
  createGmeetChat, sendGmeetChatMessage, prepareGmeetChatSend, openGmeetChatPanel,
  findGmeetChatToggle, findGmeetChatContainer, findGmeetChatComposer, findGmeetChatSendButton,
  type GmeetChatMessage,
} from './gmeet-chat.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string) => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

const FIX = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures');
const g = globalThis as any;
let intervalCb: (() => void) | null = null;
g.setInterval = (cb: () => void) => { intervalCb = cb; return 1; };
g.clearInterval = () => { intervalCb = null; };
const flush = () => new Promise<void>((r) => setTimeout(r, 0));   // jsdom MutationObserver callbacks are microtasks

/** The English-only selectors the reader and sender shipped with before they were made language-free. */
const ENGLISH_ONLY = [
  'button[aria-label*="Chat with everyone" i]',
  'textarea[aria-label*="Send a message" i]',
  'button[aria-label*="Send a message" i]',
  '[aria-live="polite"][aria-label*="chat" i]',
];

interface Case {
  lang: string;
  toggleVia: string; listVia: string; composerVia: string; sendVia: string;
  self: string;                  // how this fixture labels the local participant
  person: string; line: string;  // a participant's new line
}
const CASES: Case[] = [
  { lang: 'en', toggleVia: 'attr:data-panel-id', listVia: '[jsname="xySENc"]', composerVia: 'structure:panel-of-message-list', sendVia: 'icon:send', self: 'You', person: 'Bob', line: 'can the bot read this?' },
  { lang: 'nl', toggleVia: 'attr:data-panel-id', listVia: '[jsname="xySENc"]', composerVia: 'structure:panel-of-message-list', sendVia: 'icon:send', self: 'Jij', person: 'Sanne', line: 'kan de bot dit lezen? ë' },
  { lang: 'de', toggleVia: 'icon:chat', listVia: 'structure:live-region-of-message', composerVia: 'structure:panel-of-message-list', sendVia: 'icon:send', self: 'Sie', person: 'Lena', line: 'kann der Bot das lesen? ß' },
  { lang: 'xx', toggleVia: 'attr:data-panel-id', listVia: '[jsname="xySENc"]', composerVia: 'structure:panel-of-message-list', sendVia: 'structure:only-button-of-row', self: 'Te', person: 'Réka', line: 'olvassa ezt a bot?' },
];

const group = (doc: Document, sender: string, id: string, text: string): Element => {
  const g0 = doc.createElement('div');
  g0.className = 'Ss4fHf';
  g0.innerHTML = `<div class="HNucUd"><div class="poVWob"></div><div class="MuzmKe">10:43</div></div>`
    + `<div class="beTDc"><div class="RLrADb" data-message-id="${id}"><div jsname="dTKtvb"><div></div></div></div></div>`;
  g0.querySelector('.poVWob')!.textContent = sender;
  g0.querySelector('[jsname="dTKtvb"] > div')!.textContent = text;
  return g0;
};

for (const c of CASES) {
  console.log(`\n=== ${c.lang} ===`);
  const dom = new JSDOM(readFileSync(path.join(FIX, `meet-chat-${c.lang}.html`), 'utf8'));
  const w = dom.window;
  const doc = w.document;
  g.document = doc;
  g.MutationObserver = w.MutationObserver;
  g.HTMLTextAreaElement = w.HTMLTextAreaElement;
  g.Event = w.Event;
  g.KeyboardEvent = w.KeyboardEvent;
  const byFx = (id: string) => doc.querySelector(`[data-fixture-id="${id}"]`) as HTMLElement;
  const clicks: string[] = [];
  doc.addEventListener('click', (ev) => {
    const b = (ev.target as Element).closest('button');
    clicks.push(b?.getAttribute('data-fixture-id') || b?.getAttribute('aria-label') || '?');
  });

  if (c.lang !== 'en') {
    const hits = ENGLISH_ONLY.map((s) => doc.querySelectorAll(s).length);
    check(`${c.lang}: the old English-only selectors match nothing here`, hits.every((n) => n === 0), JSON.stringify(hits));
  }

  // Lookups, and the layer that found each.
  const toggle = findGmeetChatToggle();
  const list = findGmeetChatContainer();
  const composer = findGmeetChatComposer();
  const send = composer ? findGmeetChatSendButton(composer.el) : null;
  check(`${c.lang}: toggle found via ${c.toggleVia}`, toggle?.el === byFx('toggle') && toggle?.via === c.toggleVia, `via=${toggle?.via}`);
  check(`${c.lang}: message list found via ${c.listVia}`, list?.el === byFx('list') && list?.via === c.listVia, `via=${list?.via}`);
  check(`${c.lang}: composer found via ${c.composerVia}`, composer?.el === byFx('composer') && composer?.via === c.composerVia, `via=${composer?.via}`);
  check(`${c.lang}: send button found via ${c.sendVia}`, send?.el === byFx('send') && send?.via === c.sendVia, `via=${send?.via}`);

  // Reader.
  const got: GmeetChatMessage[] = [];
  const chat = createGmeetChat({ selfName: 'Vexa', onMessage: (m) => got.push(m) });
  check(`${c.lang}: history on screen is primed, not emitted`, chat.getState().primed && got.length === 0, JSON.stringify(chat.getState()));
  byFx('list').append(group(doc, c.person, `${c.lang}-1`, c.line));
  await flush();
  check(`${c.lang}: a participant's new line is emitted once with sender + text`,
    got.length === 1 && got[0].sender === c.person && got[0].text === c.line, JSON.stringify(got));
  intervalCb?.(); await flush();
  check(`${c.lang}: a poll does not re-emit it`, got.length === 1, JSON.stringify(got));
  byFx('list').append(group(doc, c.self, `${c.lang}-2`, 'typed on the bot by hand'));
  await flush();
  if (c.lang === 'xx') {
    // A language outside the label set: its "You" is not recognised — the bot's own lines are still
    // caught by the recent-sent set and the display name (below), which carry no language at all.
    check(`${c.lang}: an unlisted self label ("${c.self}") is read as a participant (documented limit)`, got.length === 2, JSON.stringify(got));
  } else {
    check(`${c.lang}: the localised self label "${c.self}" is skipped`, got.length === 1, JSON.stringify(got));
  }

  // In-page sender: fills the composer, clicks the row's send button (never the reaction button).
  clicks.length = 0;
  const ok = await sendGmeetChatMessage(`bot line ${c.lang}`);
  check(`${c.lang}: in-page send fills the composer and clicks the row's send button`,
    ok && (byFx('composer') as HTMLTextAreaElement).value === `bot line ${c.lang}` && JSON.stringify(clicks) === '["send"]',
    `ok=${ok} clicks=${JSON.stringify(clicks)}`);
  const before = got.length;
  byFx('list').append(group(doc, 'Vexa Notetaker (other label)', `${c.lang}-3`, `bot line ${c.lang}`));
  await flush();
  check(`${c.lang}: the bot's own just-sent line is not echoed (any sender label)`, got.length === before, JSON.stringify(got));

  // Trusted-input preparation marks the same two elements.
  const prep = await prepareGmeetChatSend(`trusted ${c.lang}`);
  check(`${c.lang}: prepareGmeetChatSend marks the composer and the send button`,
    prep.composer === c.composerVia && prep.send === c.sendVia
      && byFx('composer').hasAttribute('data-vexa-chat-composer') && byFx('send').hasAttribute('data-vexa-chat-send'),
    JSON.stringify(prep));
  byFx('list').append(group(doc, 'Someone', `${c.lang}-4`, `trusted ${c.lang}`));
  await flush();
  check(`${c.lang}: the trusted line is not echoed either`, got.length === before, JSON.stringify(got));
  chat.destroy();

  // A closed panel: no composer, toggle unpressed → the language-free toggle is clicked.
  const aside = doc.querySelector('aside')!;
  aside.remove();
  byFx('toggle').setAttribute('aria-pressed', 'false');
  clicks.length = 0;
  check(`${c.lang}: a closed panel is opened through the toggle`, openGmeetChatPanel() && JSON.stringify(clicks) === '["toggle"]', JSON.stringify(clicks));
}

console.log(failed ? `\n❌ ${failed} check(s) failed` : '\n✅ gmeet-chat-locales: all checks passed');
process.exit(failed ? 1 : 0);

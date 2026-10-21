/**
 * Google Meet chat reader + sender — SHARED browser module, mirror of
 * jitsi-chat.ts / teams-chat.ts. Emits each new chat message from another
 * participant as { sender, text }, and writes into the meeting chat.
 *
 * Pure DOM — Meet exposes no in-page chat API, and its chat panel must be OPEN
 * for messages to be in the DOM. The reader therefore opens the panel itself
 * (openGmeetChatPanel) on start and re-checks on every poll, because Meet
 * closes it on some layout changes.
 *
 * LANGUAGE-INDEPENDENT. Meet localises every label to the viewer's UI language
 * (the bot's is BOT_UI_LOCALE), so no lookup depends on an English string. Each
 * lookup walks layers in this order, and getState() / the send log say which hit:
 *   1. stable attributes — `data-message-id`, `jsname`, `data-panel-id`, `aria-live`;
 *   2. structure — the composer is the text field of the panel that holds the
 *      message list; the send button is the button of the composer's own row;
 *   3. icon ligatures — Meet's material-icon buttons carry the icon NAME as text
 *      (`chat`, `send`), the same in every UI language;
 *   4. a multilingual label set (en, nl, de, fr, es, ru, pt, it) — last, for a
 *      build that drops the hooks above.
 *
 * Identity: every message element carries Meet's stable `data-message-id`; that
 * id is the dedup key, with sender+text as the fallback for an element without
 * one. A run of messages from one person shares a single sender header, so the
 * sender is read from the nearest enclosing group.
 *
 * The bot's OWN messages are never emitted: every text a send path writes is held
 * in a short recent-sent set the reader consumes (rememberGmeetChatSent), and a
 * message under the bot's display name or Meet's localised "You" label is skipped —
 * so an embedder that auto-replies to chat cannot converse with itself.
 */

export interface GmeetChatMessage { sender: string; text: string }

export interface GmeetChatOptions {
  log?: (m: string) => void;
  onMessage: (msg: GmeetChatMessage) => void;
  /** The bot's display name — a message under it is the bot's own, like "You". */
  selfName?: string;
  /** Poll interval (ms) for the panel re-check + rescan. Default 2000. */
  pollMs?: number;
  /** Minimum gap (ms) between attempts to re-open a closed panel. Default 10000. */
  reopenMs?: number;
}

export interface GmeetChat {
  destroy(): void;
  getState(): {
    matchedContainer: string | null;
    matchedComposer: string | null;
    panelOpen: boolean;
    primed: boolean;
    seen: number;
    recent: GmeetChatMessage[];
    candidates: Array<{ sel: string; count: number }>;
  };
}

/** A lookup's result: the element and the layer that found it. */
export interface GmeetChatHit { el: HTMLElement; via: string }

// ── Layer 1: stable attributes (plain CSS, language-free) ──
/** The chat side-panel toggle: Meet keys each side-panel button by `data-panel-id`; chat is 2. */
export const gmeetChatToggleSelectors: string[] = [
  'button[data-panel-id="2"]',
  '[role="button"][data-panel-id="2"]',
];
export const gmeetChatContainerSelectors: string[] = [
  '[jsname="xySENc"]',
  '[role="list"][aria-live="polite"]',
];
export const gmeetChatMessageSelectors: string[] = [
  '[data-message-id]',
  '[jsname="dTKtvb"]',
];
export const gmeetChatSenderSelectors: string[] = [
  '[data-sender-name]',
  '.poVWob',
  '.YTbUzc',
  '[class*="sender"]',
];
export const gmeetChatTextSelectors: string[] = [
  '[jsname="dTKtvb"]',
  '[data-message-text]',
];
/** Fields that can be the composer — chosen by structure (which panel holds them), never by label. */
export const gmeetChatComposerFieldSelectors: string[] = [
  'textarea',
  '[contenteditable="true"][role="textbox"]',
  '[contenteditable="true"]',
];

// ── Layer 3: icon ligatures (the icon's NAME, identical in every UI language) ──
export const gmeetChatToggleIcons: string[] = ['chat', 'chat_bubble', 'chat_bubble_outline', 'forum'];
export const gmeetChatSendIcons: string[] = ['send'];

// ── Layer 4: multilingual labels (en, nl, de, fr, es, ru, pt, it), case-folded substrings ──
/** Words in the chat toggle's label ("Chat with everyone"). Only matched on a toggle button. */
export const gmeetChatToggleLabels: string[] = [
  'chat',                  // en Chat with everyone · nl Chatten met iedereen · de Mit allen chatten · es Chatear · it Chatta
  'discuter',              // fr Discuter avec tous les participants
  'чат',                   // ru Чат со всеми
  'conversa',              // pt Conversar com todos
];
/** Words in the send button's / composer's label ("Send a message"). */
export const gmeetChatSendLabels: string[] = [
  'send',                                  // en Send a message
  'verzend', 'versturen', 'verstuur', 'stuur', // nl Bericht verzenden / Een bericht sturen
  'senden', 'nachricht',                   // de Nachricht senden
  'envoyer',                               // fr Envoyer un message
  'enviar',                                // es / pt
  'отправ', 'сообщени',                    // ru Отправить сообщение
  'invia',                                 // it Invia un messaggio
];
/** How Meet may label the local participant's own messages, per UI language (whole label). The
 *  recent-sent set and the bot's display name catch the bot's lines when this list misses. */
export const gmeetChatSelfLabels: string[] = [
  'you',                   // en
  'jij', 'u',              // nl
  'sie', 'du', 'ich',      // de (Meet's German UI addresses the viewer as "Sie")
  'vous', 'moi',           // fr
  'tú', 'tu', 'yo',        // es / it
  'вы', 'я',               // ru
  'você', 'eu',            // pt
  'io',                    // it
];

/** How long a sent text stays claimable as the bot's own echo (ms). */
const RECENT_SENT_TTL_MS = 120_000;

// Texts this page just sent → when. Shared by every send path and every reader in the page.
const recentSent = new Map<string, number>();
const normalize = (t: string): string => t.replace(/\s+/g, ' ').trim();
const fold = (t: string): string => normalize(t).toLowerCase();

/** Record `text` as the bot's own line so the reader skips its echo. Every send path calls it. */
export function rememberGmeetChatSent(text: string): void {
  const now = Date.now();
  for (const [t, at] of recentSent) if (now - at > RECENT_SENT_TTL_MS) recentSent.delete(t);
  recentSent.set(normalize(text), now);
}

/** True (and the entry is consumed) when `text` is one this page sent within the TTL. */
function consumeSent(text: string): boolean {
  const key = normalize(text);
  const at = recentSent.get(key);
  if (at === undefined) return false;
  recentSent.delete(key);
  return Date.now() - at <= RECENT_SENT_TTL_MS;
}

const firstMatch = (root: ParentNode, selectors: string[]): Element | null => {
  for (const s of selectors) {
    const el = root.querySelector(s);
    if (el) return el;
  }
  return null;
};

/** An element's accessible label (aria-label, else Meet's tooltip attribute), case-folded. */
const labelOf = (el: Element): string =>
  fold(el.getAttribute('aria-label') || el.getAttribute('data-tooltip') || el.getAttribute('title') || '');

/** The material-icon ligature a button renders (`<i …>send</i>`), case-folded — or ''. */
export function iconOf(el: Element): string {
  for (const i of Array.from(el.querySelectorAll('i'))) {
    const t = fold(i.textContent || '');
    if (t && /^[a-z_]+$/.test(t)) return t;
  }
  return '';
}

const hasWord = (label: string, words: string[]): boolean => !!label && words.some((w) => label.includes(w));

const isDisabled = (el: Element): boolean =>
  el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true';

/** The message list: a stable container selector, else the live region / list holding a message. */
export function findGmeetChatContainer(): { el: Element; via: string } | null {
  for (const sel of gmeetChatContainerSelectors) {
    const el = document.querySelector(sel);
    if (el) return { el, via: sel };
  }
  const msg = firstMatch(document, gmeetChatMessageSelectors);
  for (let cur = msg?.parentElement ?? null, i = 0; cur && i < 8; cur = cur.parentElement, i++) {
    if (cur.hasAttribute('aria-live') || cur.getAttribute('role') === 'list') return { el: cur, via: 'structure:live-region-of-message' };
  }
  return null;
}

/** Text fields inside `root`, in selector order, de-duplicated, enabled. */
const fieldsIn = (root: ParentNode): Element[] => {
  const out: Element[] = [];
  for (const sel of gmeetChatComposerFieldSelectors) {
    for (const el of Array.from(root.querySelectorAll(sel))) if (!out.includes(el) && !isDisabled(el)) out.push(el);
  }
  return out;
};

/** The chat composer, by structure: the text field of the panel that holds the message list; else
 *  the document's only text field (Meet's in-call page has no other); else a field whose label is
 *  a "send a message" label in a supported language. */
export function findGmeetChatComposer(): GmeetChatHit | null {
  const list = findGmeetChatContainer()?.el ?? null;
  for (let cur = list?.parentElement ?? null, i = 0; cur && i < 8; cur = cur.parentElement, i++) {
    const f = fieldsIn(cur).filter((el) => !(list!.contains?.(el)));
    if (f.length) return { el: f[0] as HTMLElement, via: 'structure:panel-of-message-list' };
  }
  const all = fieldsIn(document);
  if (all.length === 1) return { el: all[0] as HTMLElement, via: 'structure:only-text-field' };
  const labelled = all.find((el) => hasWord(labelOf(el), gmeetChatSendLabels));
  if (labelled) return { el: labelled as HTMLElement, via: 'label:multilingual' };
  return null;
}

/** The send button of `composer`'s own row: climb from the composer to the nearest ancestor that
 *  holds a button; of that row's buttons take the `send` icon, else a multilingual send label, else
 *  the row's only button. Searching outward from the composer keeps toolbar controls (e.g. the
 *  reactions button) out of reach; a row of several unidentifiable buttons yields null (Enter). */
export function findGmeetChatSendButton(composer: Element): GmeetChatHit | null {
  for (let cur: Element | null = composer.parentElement, i = 0; cur && i < 5; cur = cur.parentElement, i++) {
    const buttons = Array.from(cur.querySelectorAll('button, [role="button"]'));
    if (!buttons.length) continue;
    const byIcon = buttons.find((b) => gmeetChatSendIcons.includes(iconOf(b)));
    if (byIcon) return { el: byIcon as HTMLElement, via: 'icon:send' };
    const byLabel = buttons.find((b) => hasWord(labelOf(b), gmeetChatSendLabels));
    if (byLabel) return { el: byLabel as HTMLElement, via: 'label:multilingual' };
    if (buttons.length === 1) return { el: buttons[0] as HTMLElement, via: 'structure:only-button-of-row' };
    return null;
  }
  return null;
}

/** The chat panel toggle: `data-panel-id`, else a button showing a chat icon, else a toggle button
 *  whose label carries a chat word in a supported language. */
export function findGmeetChatToggle(): GmeetChatHit | null {
  const byAttr = firstMatch(document, gmeetChatToggleSelectors);
  if (byAttr) return { el: byAttr as HTMLElement, via: 'attr:data-panel-id' };
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
  const byIcon = buttons.find((b) => gmeetChatToggleIcons.includes(iconOf(b)));
  if (byIcon) return { el: byIcon as HTMLElement, via: 'icon:chat' };
  const byLabel = buttons.find((b) => b.hasAttribute('aria-pressed') && hasWord(labelOf(b), gmeetChatToggleLabels));
  if (byLabel) return { el: byLabel as HTMLElement, via: 'label:multilingual' };
  return null;
}

/** Open the chat panel when it is closed. Returns true when the panel is open or a toggle was
 *  clicked; false when no composer and no toggle is in the DOM. */
export function openGmeetChatPanel(): boolean {
  if (findGmeetChatComposer()) return true;
  const toggle = findGmeetChatToggle();
  if (!toggle) return false;
  // A pressed toggle means the panel is already open (and still mounting) — clicking would close it.
  if (toggle.el.getAttribute('aria-pressed') === 'true') return true;
  toggle.el.click();
  return true;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Open the panel if needed and wait (up to `waitMs`) for the composer. */
async function awaitComposer(waitMs: number): Promise<GmeetChatHit | null> {
  let composer = findGmeetChatComposer();
  if (!composer && openGmeetChatPanel()) {
    const deadline = Date.now() + waitMs;
    while (!composer && Date.now() < deadline) {
      await sleep(100);
      composer = findGmeetChatComposer();
    }
  }
  return composer;
}

/** Attributes the bot's trusted-input path addresses the composer and the send button by. */
export const GMEET_CHAT_COMPOSER_MARK = 'data-vexa-chat-composer';
export const GMEET_CHAT_SEND_MARK = 'data-vexa-chat-send';

/** Prepare a TRUSTED send driven from outside the page (Playwright fill + click / Enter): open the
 *  panel, find the composer and its send button, mark them with GMEET_CHAT_COMPOSER_MARK /
 *  GMEET_CHAT_SEND_MARK, and record `text` as the bot's own line. Returns the layer that found
 *  each (null when absent). */
export async function prepareGmeetChatSend(
  text: string,
  opts: { log?: (m: string) => void; waitMs?: number } = {},
): Promise<{ composer: string | null; send: string | null }> {
  const log = opts.log || (() => {});
  for (const m of Array.from(document.querySelectorAll(`[${GMEET_CHAT_COMPOSER_MARK}], [${GMEET_CHAT_SEND_MARK}]`))) {
    m.removeAttribute?.(GMEET_CHAT_COMPOSER_MARK);
    m.removeAttribute?.(GMEET_CHAT_SEND_MARK);
  }
  const composer = await awaitComposer(opts.waitMs ?? 3000);
  if (!composer) { log('chat send: no composer found'); return { composer: null, send: null }; }
  composer.el.setAttribute(GMEET_CHAT_COMPOSER_MARK, '1');
  const send = findGmeetChatSendButton(composer.el);
  if (send) send.el.setAttribute(GMEET_CHAT_SEND_MARK, '1');
  rememberGmeetChatSent(text);
  log(`chat send: composer via ${composer.via}, send button via ${send?.via ?? 'none (Enter)'}`);
  return { composer: composer.via, send: send?.via ?? null };
}

/** Write `text` into the meeting chat from INSIDE the page: open the panel if needed, fill the
 *  composer, click its send button, else press Enter. Returns false when no composer can be found. */
export async function sendGmeetChatMessage(
  text: string,
  opts: { log?: (m: string) => void; waitMs?: number } = {},
): Promise<boolean> {
  const log = opts.log || (() => {});
  if (!normalize(text)) return false;
  const found = await awaitComposer(opts.waitMs ?? 3000);
  if (!found) { log('chat send: no composer found'); return false; }
  const composer = found.el;

  composer.focus?.();
  if (composer.getAttribute('contenteditable') === 'true') {
    composer.textContent = text;
  } else {
    // Meet's input is framework-controlled: set through the NATIVE value setter so the
    // framework's tracker sees a change, then announce it with an input event.
    const Ta = typeof HTMLTextAreaElement !== 'undefined' ? HTMLTextAreaElement : null;
    const setter = Ta ? Object.getOwnPropertyDescriptor(Ta.prototype, 'value')?.set : undefined;
    if (setter && Ta && composer instanceof Ta) setter.call(composer, text);
    else (composer as HTMLTextAreaElement).value = text;
  }
  composer.dispatchEvent(new Event('input', { bubbles: true }));
  rememberGmeetChatSent(text);

  const send = findGmeetChatSendButton(composer);
  if (send && !isDisabled(send.el)) {
    send.el.click();
    log(`chat send: composer via ${found.via}, clicked send via ${send.via} (${text.slice(0, 60)})`);
    return true;
  }
  const init = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true };
  const enter = typeof KeyboardEvent === 'function'
    ? new KeyboardEvent('keydown', init)
    : Object.assign(new Event('keydown', init), { key: 'Enter', code: 'Enter', keyCode: 13 });
  composer.dispatchEvent(enter);
  log(`chat send: composer via ${found.via}, dispatched Enter (${text.slice(0, 60)})`);
  return true;
}

export function createGmeetChat(opts: GmeetChatOptions): GmeetChat {
  const log = opts.log || (() => {});
  const selfName = fold(opts.selfName || '');
  const reopenMs = opts.reopenMs ?? 10_000;
  const seenKeys = new Set<string>();
  const recent: GmeetChatMessage[] = [];
  let matchedContainer: string | null = null;
  let matchedComposer: string | null = null;
  let container: Element | null = null;
  let panelOpen = false;
  let primed = false;
  let lastOpenAttempt = -Infinity;

  const textOf = (el: Element | null): string => normalize(el?.textContent || '');

  // The sender header is shared by a run of messages: climb from the message to the
  // nearest ancestor that names a sender (its own attribute, or a header inside it).
  const senderOf = (node: Element): string => {
    let cur: Element | null = node;
    for (let i = 0; i < 5 && cur; i++, cur = cur.parentElement) {
      const attr = normalize(cur.getAttribute('data-sender-name') || '');
      if (attr) return attr;
      if (cur === node) continue;   // a header lives beside the message, never inside it
      const t = textOf(firstMatch(cur, gmeetChatSenderSelectors));
      if (t) return t;
    }
    return '';
  };

  const keyOf = (node: Element, sender: string, text: string): string => {
    const id = node.getAttribute('data-message-id');
    return id ? `id:${id}` : `txt:${sender}\u0000${text}`;
  };

  const extract = (node: Element): { msg: GmeetChatMessage; key: string } | null => {
    const textEl = node.matches?.(gmeetChatTextSelectors.join(',')) ? node : firstMatch(node, gmeetChatTextSelectors);
    const text = textOf(textEl ?? node);
    if (!text) return null;   // not rendered yet — left unseen so a later scan reads it
    const sender = senderOf(node) || 'Unknown';
    return { msg: { sender, text }, key: keyOf(node, sender, text) };
  };

  const isOwn = (msg: GmeetChatMessage): boolean => {
    const s = fold(msg.sender);
    return gmeetChatSelfLabels.includes(s) || (!!selfName && s === selfName);
  };

  const messageNodes = (root: ParentNode): Element[] => {
    for (const sel of gmeetChatMessageSelectors) {
      const nodes = Array.from(root.querySelectorAll(sel));
      if (nodes.length) return nodes;
    }
    return [];
  };

  const scan = () => {
    const root: ParentNode = container ?? document;
    for (const node of messageNodes(root)) {
      const got = extract(node);
      if (!got || seenKeys.has(got.key)) continue;
      seenKeys.add(got.key);
      // What is on screen when the panel first opens predates the reader — recorded, not emitted.
      if (!primed) continue;
      const justSent = consumeSent(got.msg.text);   // consumed either way, so it cannot swallow a later echo
      if (isOwn(got.msg) || justSent) continue;
      recent.push(got.msg);
      if (recent.length > 30) recent.shift();
      log(`chat ${got.msg.sender}: ${got.msg.text.slice(0, 60)}`);
      try { opts.onMessage(got.msg); } catch { /* never break capture */ }
    }
  };

  const observer = new MutationObserver(() => { if (primed) scan(); });

  const tick = () => {
    const found = findGmeetChatContainer();
    const composer = findGmeetChatComposer();
    matchedComposer = composer?.via ?? null;
    panelOpen = !!(found || composer || messageNodes(document).length);
    if (!panelOpen) {
      const now = Date.now();
      if (now - lastOpenAttempt >= reopenMs) {
        lastOpenAttempt = now;
        const toggle = findGmeetChatToggle();
        if (openGmeetChatPanel()) log(`chat panel closed — opened it (toggle via ${toggle?.via ?? 'none'})`);
      }
      return;
    }
    if (found && found.el !== container) {
      container = found.el;
      matchedContainer = found.via;
      observer.disconnect();
      observer.observe(container, { childList: true, subtree: true, characterData: true });
      log(`chat container matched: ${matchedContainer}`);
    }
    scan();
    if (!primed) { primed = true; log(`chat primed (${seenKeys.size} message(s) already on screen; composer via ${matchedComposer ?? 'none'})`); }
  };

  tick();
  const poll = setInterval(tick, opts.pollMs ?? 2000);

  return {
    destroy() { clearInterval(poll); observer.disconnect(); },
    getState() {
      return {
        matchedContainer,
        matchedComposer,
        panelOpen,
        primed,
        seen: seenKeys.size,
        recent: recent.slice(-10),
        candidates: gmeetChatContainerSelectors.map((sel) => ({ sel, count: document.querySelectorAll(sel).length })),
      };
    },
  };
}

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
 * Identity: every message element carries Meet's stable `data-message-id`; that
 * id is the dedup key, with sender+text as the fallback for an element without
 * one. A run of messages from one person shares a single sender header, so the
 * sender is read from the nearest enclosing group. Meet's class names are
 * obfuscated and change across builds, so every lookup walks candidate
 * selectors most-specific first, and getState() reports what matched.
 *
 * The bot's OWN messages are never emitted: Meet labels them "You", and every
 * text sendGmeetChatMessage writes is held in a short recent-sent set the reader
 * consumes — so an embedder that auto-replies to chat cannot converse with itself.
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
    panelOpen: boolean;
    primed: boolean;
    seen: number;
    recent: GmeetChatMessage[];
    candidates: Array<{ sel: string; count: number }>;
  };
}

// Candidate selectors, most-specific first.
export const gmeetChatToggleSelectors: string[] = [
  'button[aria-label*="Chat with everyone" i]',
  'button[aria-label*="chat" i][aria-pressed="false"]',
];
export const gmeetChatContainerSelectors: string[] = [
  '[jsname="xySENc"]',
  '[role="list"][aria-live="polite"]',
  '[aria-live="polite"][aria-label*="chat" i]',
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
export const gmeetChatComposerSelectors: string[] = [
  'textarea[aria-label*="Send a message" i]',
  'textarea[aria-label*="message" i]',
  '[contenteditable="true"][aria-label*="message" i]',
];
export const gmeetChatSendSelectors: string[] = [
  'button[aria-label*="Send a message" i]',
  'button[aria-label*="Send" i]',
];

/** How Meet labels the local participant's own messages. */
const SELF_LABEL = /^you$/i;
/** How long a sent text stays claimable as the bot's own echo (ms). */
const RECENT_SENT_TTL_MS = 120_000;

// Texts this page just sent → when. Shared by the sender and every reader in the page.
const recentSent = new Map<string, number>();
const normalize = (t: string): string => t.replace(/\s+/g, ' ').trim();

function rememberSent(text: string): void {
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

const findComposer = (): HTMLElement | null => firstMatch(document, gmeetChatComposerSelectors) as HTMLElement | null;

/** Open the "Chat with everyone" panel when it is closed. Returns true when the panel is
 *  open or a toggle was clicked; false when no composer and no toggle is in the DOM. */
export function openGmeetChatPanel(): boolean {
  if (findComposer()) return true;
  for (const sel of gmeetChatToggleSelectors) {
    const btn = document.querySelector(sel) as HTMLElement | null;
    if (!btn) continue;
    // A pressed toggle means the panel is already open (and still mounting) — clicking would close it.
    if (btn.getAttribute('aria-pressed') === 'true') return true;
    btn.click();
    return true;
  }
  return false;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Write `text` into the meeting chat: open the panel if needed, fill the composer, send.
 *  Returns false when no composer can be found. */
export async function sendGmeetChatMessage(
  text: string,
  opts: { log?: (m: string) => void; waitMs?: number } = {},
): Promise<boolean> {
  const log = opts.log || (() => {});
  if (!normalize(text)) return false;
  let composer = findComposer();
  if (!composer && openGmeetChatPanel()) {
    const deadline = Date.now() + (opts.waitMs ?? 3000);
    while (!composer && Date.now() < deadline) {
      await sleep(100);
      composer = findComposer();
    }
  }
  if (!composer) { log('chat send: no composer found'); return false; }

  composer.focus?.();
  if (composer.getAttribute('contenteditable') === 'true') {
    composer.textContent = text;
  } else {
    // Meet's input is framework-controlled: set through the NATIVE value setter so the
    // framework's tracker sees a change, then announce it with an input event.
    const proto = typeof HTMLTextAreaElement !== 'undefined' ? HTMLTextAreaElement.prototype : null;
    const setter = proto ? Object.getOwnPropertyDescriptor(proto, 'value')?.set : undefined;
    if (setter) setter.call(composer, text);
    else (composer as HTMLTextAreaElement).value = text;
  }
  composer.dispatchEvent(new Event('input', { bubbles: true }));
  rememberSent(text);

  // The send button sits beside the composer; searching near it first keeps the broad
  // "Send" candidate from matching an unrelated control (e.g. "Send a reaction").
  let sendBtn: HTMLElement | null = null;
  for (let cur: Element | null = composer.parentElement, i = 0; cur && i < 5 && !sendBtn; cur = cur.parentElement, i++) {
    sendBtn = firstMatch(cur, gmeetChatSendSelectors) as HTMLElement | null;
  }
  if (sendBtn && !sendBtn.hasAttribute('disabled') && sendBtn.getAttribute('aria-disabled') !== 'true') {
    sendBtn.click();
    log(`chat send: clicked send (${text.slice(0, 60)})`);
    return true;
  }
  const init = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true };
  const enter = typeof KeyboardEvent === 'function'
    ? new KeyboardEvent('keydown', init)
    : Object.assign(new Event('keydown', init), { key: 'Enter', code: 'Enter', keyCode: 13 });
  composer.dispatchEvent(enter);
  log(`chat send: dispatched Enter (${text.slice(0, 60)})`);
  return true;
}

export function createGmeetChat(opts: GmeetChatOptions): GmeetChat {
  const log = opts.log || (() => {});
  const selfName = normalize(opts.selfName || '');
  const reopenMs = opts.reopenMs ?? 10_000;
  const seenKeys = new Set<string>();
  const recent: GmeetChatMessage[] = [];
  let matchedContainer: string | null = null;
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

  const isOwn = (msg: GmeetChatMessage): boolean =>
    SELF_LABEL.test(msg.sender) || (!!selfName && normalize(msg.sender) === selfName);

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

  const findContainer = (): Element | null => {
    for (const sel of gmeetChatContainerSelectors) {
      const el = document.querySelector(sel);
      if (el) { matchedContainer = sel; return el; }
    }
    return null;
  };

  const observer = new MutationObserver(() => { if (primed) scan(); });

  const tick = () => {
    const found = findContainer();
    panelOpen = !!(found || findComposer() || messageNodes(document).length);
    if (!panelOpen) {
      const now = Date.now();
      if (now - lastOpenAttempt >= reopenMs) {
        lastOpenAttempt = now;
        if (openGmeetChatPanel()) log('chat panel closed — opened it');
      }
      return;
    }
    if (found && found !== container) {
      container = found;
      observer.disconnect();
      observer.observe(container, { childList: true, subtree: true, characterData: true });
      log(`chat container matched: ${matchedContainer}`);
    }
    scan();
    if (!primed) { primed = true; log(`chat primed (${seenKeys.size} message(s) already on screen)`); }
  };

  tick();
  const poll = setInterval(tick, opts.pollMs ?? 2000);

  return {
    destroy() { clearInterval(poll); observer.disconnect(); },
    getState() {
      return {
        matchedContainer,
        panelOpen,
        primed,
        seen: seenKeys.size,
        recent: recent.slice(-10),
        candidates: gmeetChatContainerSelectors.map((sel) => ({ sel, count: document.querySelectorAll(sel).length })),
      };
    },
  };
}

/**
 * MS Teams chat reader — SHARED browser module (bot + extension), mirror of
 * zoom-chat.ts. Watches the Teams chat / meeting-chat panel and emits each new
 * message as { sender, text }. The extension wraps these into capture.v1 `chat`
 * MeetingEvents (kind:'chat', speaker:sender, text). Pure DOM observation — no
 * audio, no network.
 *
 * Teams' DOM is data-tid-driven but changes across builds, so extraction is
 * defensive: candidate container/message/sender/body selectors, plus a heuristic
 * fallback (largest leaf text = body, short text = sender). The chat panel must
 * be OPEN for messages to be in the DOM. getState() surfaces what matched + a
 * structural dump so selectors can be tuned from live telemetry.
 */

export interface TeamsChatMessage { sender: string; text: string }

export interface TeamsChatOptions {
  log?: (m: string) => void;
  onMessage: (msg: TeamsChatMessage) => void;
}

export interface TeamsChat {
  destroy(): void;
  getState(): {
    matchedContainer: string | null;
    seen: number;
    recent: TeamsChatMessage[];
    candidates: Array<{ sel: string; count: number }>;
    sample: { sel: string; structure: string[] } | null;
  };
}

// Candidate selectors, most-specific first. Teams has used several over builds.
const CONTAINER_SELECTORS = [
  '[data-tid="chat-pane-list"]',
  '[data-tid="message-pane-list-runway"]',
  '[data-tid="chatPaneMessageList"]',
  '[role="log"]',
  '[aria-label*="Chat messages"]',
  '[class*="chat-pane-list"]',
  '[class*="messageList"]',
];
const MESSAGE_SELECTORS = [
  '[data-tid="chat-pane-message"]',
  'div[data-tid^="chat-pane-message"]',
  'div[data-mid]',
  '[data-tid="message"]',
  '[class*="chat-message"]',
  '[role="listitem"]',
];
const SENDER_SELECTORS = [
  '[data-tid="message-author-name"]',
  '[data-tid*="author"]',
  '[class*="author-name"]',
  '[class*="authorName"]',
  '[class*="sender"]',
  '[class*="display-name"]',
];
const TEXT_SELECTORS = [
  '[data-tid="messageBodyContent"]',
  '[id^="content-"]',
  '[class*="messageBody"]',
  '[class*="message-body"]',
  '[class*="messageText"]',
  'div[dir="auto"]',
];

export function createTeamsChat(opts: TeamsChatOptions): TeamsChat {
  const log = opts.log || (() => {});
  const seenNodes = new WeakSet<Element>();
  const seenHashes = new Set<string>();
  const recent: TeamsChatMessage[] = [];
  let matchedContainer: string | null = null;
  let container: Element | null = null;

  const firstText = (root: Element, selectors: string[]): string => {
    for (const s of selectors) {
      const el = root.querySelector(s);
      const t = el?.textContent?.trim();
      if (t) return t;
    }
    return '';
  };

  const senderFromAria = (node: Element): string => {
    // Teams labels message rows like "Barbara W, 10:42 AM, Hi there".
    let cur: Element | null = node;
    for (let i = 0; i < 4 && cur; i++, cur = cur.parentElement) {
      const al = cur.getAttribute?.('aria-label') || '';
      const m = al.match(/^(.+?)\s*,\s*\d{1,2}:\d{2}/);
      if (m && m[1].trim()) return m[1].trim();
    }
    return '';
  };

  const extract = (node: Element): TeamsChatMessage | null => {
    let text = firstText(node, TEXT_SELECTORS);
    let sender = firstText(node, SENDER_SELECTORS);

    // Sender is grouped: Teams shows one header for a run of messages from the
    // same person. Climb ancestors (the group wrapper carries the header), then
    // fall back to the row's aria-label.
    if (!sender) {
      let cur: Element | null = node.parentElement;
      for (let i = 0; i < 4 && cur && !sender; i++, cur = cur.parentElement) sender = firstText(cur, SENDER_SELECTORS);
    }
    if (!sender) sender = senderFromAria(node);

    // Body fallback: largest leaf text in the node.
    if (!text) {
      const frags = Array.from(node.querySelectorAll('*'))
        .map((e) => (e.childElementCount === 0 ? (e.textContent || '').trim() : ''))
        .filter((t) => t.length > 0);
      if (!frags.length) return null;
      const longest = frags.reduce((a, b) => (b.length > a.length ? b : a), '');
      text = longest;
      if (!sender) {
        const shortName = frags.find((f) => f !== longest && f.length <= 40 && !/^\d{1,2}:\d{2}/.test(f));
        if (shortName) sender = shortName;
      }
    }
    // Strip a trailing timestamp Teams appends to the sender row ("Name 10:42 AM").
    sender = sender.replace(/\s*\d{1,2}:\d{2}\s*(AM|PM)?\s*$/i, '').trim() || 'Unknown';
    if (!text) return null;
    return { sender, text };
  };

  // Structural dump of one message node so selectors can be tuned against the
  // real (changing) Teams DOM.
  const dumpNode = (node: Element): string[] =>
    Array.from(node.querySelectorAll('*')).slice(0, 25).map((e) => {
      const cls = (e.getAttribute('class') || '').slice(0, 40);
      const tid = e.getAttribute('data-tid');
      const aria = e.getAttribute('aria-label');
      const t = e.childElementCount === 0 ? (e.textContent || '').trim().slice(0, 30) : '';
      return `${e.tagName.toLowerCase()}${tid ? '[tid=' + tid + ']' : ''}${cls ? '.' + cls : ''}${aria ? '[al=' + aria.slice(0, 30) + ']' : ''}${t ? ' »' + t : ''}`;
    });

  const emit = (node: Element) => {
    if (seenNodes.has(node)) return;
    seenNodes.add(node);
    const msg = extract(node);
    if (!msg) return;
    const hash = `${msg.sender} ${msg.text}`;
    if (seenHashes.has(hash)) return; // virtualized list re-renders the same item on scroll
    seenHashes.add(hash);
    recent.push(msg);
    if (recent.length > 30) recent.shift();
    log(`chat ${msg.sender}: ${msg.text.slice(0, 60)}`);
    try { opts.onMessage(msg); } catch { /* never break capture */ }
  };

  const scanMessages = (root: ParentNode) => {
    for (const sel of MESSAGE_SELECTORS) {
      const nodes = root.querySelectorAll(sel);
      if (nodes.length) { nodes.forEach((n) => emit(n)); return; }
    }
  };

  const findContainer = (): Element | null => {
    for (const sel of CONTAINER_SELECTORS) {
      const el = document.querySelector(sel);
      if (el) { matchedContainer = sel; return el; }
    }
    return null;
  };

  // The chat panel mounts/unmounts as the user toggles it — poll for the
  // container, (re)attach the observer, rescan existing messages.
  const observer = new MutationObserver(() => { if (container) scanMessages(container); });
  const attach = () => {
    const found = findContainer();
    if (found && found !== container) {
      container = found;
      observer.disconnect();
      observer.observe(container, { childList: true, subtree: true });
      scanMessages(container);
      log(`chat container matched: ${matchedContainer}`);
    } else if (found) {
      scanMessages(container!);
    }
  };
  attach();
  const poll = window.setInterval(attach, 2000);

  return {
    destroy() { window.clearInterval(poll); observer.disconnect(); },
    getState() {
      let sample: { sel: string; structure: string[] } | null = null;
      if (container) {
        for (const sel of MESSAGE_SELECTORS) {
          const n = container.querySelector(sel);
          if (n) { sample = { sel, structure: dumpNode(n) }; break; }
        }
      }
      return {
        matchedContainer,
        seen: seenHashes.size,
        recent: recent.slice(-10),
        candidates: CONTAINER_SELECTORS.map((sel) => ({ sel, count: document.querySelectorAll(sel).length })),
        sample,
      };
    },
  };
}

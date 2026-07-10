/**
 * Jitsi Meet chat reader + sender — SHARED browser module, mirror of
 * teams-chat.ts / zoom-chat.ts. Emits each new chat message as { sender, text }.
 *
 * Unlike the Teams/Zoom readers (pure DOM — their panel must be OPEN), the
 * primary source here is the app's own redux state
 * (`APP.store.getState()['features/chat'].messages`), which receives every
 * message whether or not the panel is mounted. The DOM observer
 * (#chatconversation / .chatmessage) is the fallback for builds that strip the
 * APP global — there the panel must be open, same constraint as Teams/Zoom.
 *
 * Sending uses the conference's own `sendTextMessage` (lib-jitsi-meet public
 * API) — a capability the Teams/Zoom modules don't have.
 */

export interface JitsiChatMessage { sender: string; text: string }

export interface JitsiChatOptions {
  log?: (m: string) => void;
  onMessage: (msg: JitsiChatMessage) => void;
  /** Poll interval for the redux source (ms). Default 1000. */
  pollMs?: number;
}

export interface JitsiChat {
  destroy(): void;
  getState(): { mode: "redux" | "dom" | null; seen: number; recent: JitsiChatMessage[] };
}

// DOM fallback selectors (stock jitsi chat panel; classic class names).
export const jitsiChatContainerSelectors: string[] = [
  "#chatconversation",
  '[aria-label*="Chat messages"]',
  '[class*="chat-conversation"]',
];
export const jitsiChatMessageSelectors: string[] = [
  ".chatmessage-wrapper",
  ".chatmessage",
  '[class*="chat-message-group"]',
];
export const jitsiChatSenderSelectors: string[] = [
  ".display-name",
  '[class*="display-name"]',
  '[class*="sender"]',
];
export const jitsiChatTextSelectors: string[] = [
  ".usermessage",
  '[class*="usermessage"]',
  '[class*="message-text"]',
];

/** Send a text message into the conference chat via the app's own API.
 *  Returns false when the API is unavailable (custom build / not joined). */
export function sendJitsiChatMessage(text: string): boolean {
  try {
    const room = (globalThis as any).APP?.conference?._room;
    if (room?.sendTextMessage) { room.sendTextMessage(text); return true; }
    return false;
  } catch {
    return false;
  }
}

/** Defensive read of the chat feature state → the RAW message list (jitsi's own array;
 *  append-only, so callers may consume it with a cursor). */
function rawReduxMessages(): any[] | null {
  try {
    const app = (globalThis as any).APP;
    const state = app?.store?.getState?.();
    const msgs = state?.["features/chat"]?.messages;
    return Array.isArray(msgs) ? msgs : null;
  } catch {
    return null;
  }
}

/** One redux entry → an emittable message, or null for non-messages: errors, and the
 *  bot's OWN messages (messageType 'local' — echoing them back would let an embedder
 *  that auto-replies to chat converse with itself). */
function toChatMessage(m: any): JitsiChatMessage | null {
  if (!m || typeof m.message !== "string") return null;
  if (m.messageType === "error" || m.messageType === "local") return null;
  return { sender: (m.displayName || "").trim() || "Unknown", text: m.message };
}

export function createJitsiChat(opts: JitsiChatOptions): JitsiChat {
  const log = opts.log || (() => {});
  const seenKeys = new Set<string>();
  const seenNodes = new WeakSet<Element>();
  const recent: JitsiChatMessage[] = [];
  let mode: "redux" | "dom" | null = null;
  let domContainer: Element | null = null;

  const emit = (msg: JitsiChatMessage, key: string) => {
    if (seenKeys.has(key)) return;
    seenKeys.add(key);
    recent.push(msg);
    if (recent.length > 30) recent.shift();
    log(`chat ${msg.sender}: ${msg.text.slice(0, 60)}`);
    try { opts.onMessage(msg); } catch { /* never break capture */ }
  };

  // ── redux source (primary) — cursor over jitsi's append-only message array, so a
  // tick costs O(new messages), not O(all messages ever). ──
  let primed = false;
  let cursor = 0;
  let emitSeq = 0;
  const pollRedux = (): boolean => {
    const msgs = rawReduxMessages();
    if (msgs === null) return false;
    mode = "redux";
    // First read PRIMES the cursor without emitting: history from before the
    // bot joined is not "new messages" to the embedder.
    if (!primed) { cursor = msgs.length; primed = true; return true; }
    if (msgs.length < cursor) cursor = 0; // the store was replaced (rare) — resync
    for (; cursor < msgs.length; cursor++) {
      const m = toChatMessage(msgs[cursor]);
      if (m) emit(m, `redux:${emitSeq++}`);
    }
    return true;
  };

  // ── DOM fallback ──
  const firstText = (root: Element, selectors: string[]): string => {
    for (const s of selectors) {
      const t = root.querySelector(s)?.textContent?.trim();
      if (t) return t;
    }
    return "";
  };
  const scanDom = () => {
    if (!domContainer) {
      for (const sel of jitsiChatContainerSelectors) {
        const el = document.querySelector(sel);
        if (el) { domContainer = el; mode = mode ?? "dom"; log(`chat container matched: ${sel}`); break; }
      }
    }
    if (!domContainer) return;
    for (const sel of jitsiChatMessageSelectors) {
      const nodes = domContainer.querySelectorAll(sel);
      if (!nodes.length) continue;
      nodes.forEach((node) => {
        if (seenNodes.has(node)) return;
        seenNodes.add(node);
        const text = firstText(node, jitsiChatTextSelectors);
        if (!text) return;
        const sender = firstText(node, jitsiChatSenderSelectors) || "Unknown";
        emit({ sender, text }, `${sender} ${text}`);
      });
      return;
    }
  };

  const tick = () => { if (!pollRedux()) scanDom(); };
  const poll = setInterval(tick, opts.pollMs ?? 1000);
  tick();

  return {
    destroy() { clearInterval(poll); },
    getState() { return { mode, seen: seenKeys.size, recent: recent.slice(-10) }; },
  };
}

import { Page } from 'playwright-core';
import { log } from '../utils';
import { createClient, RedisClientType } from 'redis';

/**
 * ChatMessage represents a single chat message in the meeting.
 */
export interface ChatMessage {
  sender: string;
  text: string;
  timestamp: number;
  isFromBot: boolean;
}

/**
 * Configuration for injecting chat messages into the transcription stream.
 * When provided, chat messages from other participants are published to the
 * `transcription_segments` Redis stream so they appear alongside spoken transcripts.
 */
export interface ChatTranscriptConfig {
  token: string;          // MeetingToken (HS256 JWT)
  platform: string;       // e.g. "google_meet"
  meetingId: number;      // Internal meeting ID
  connectionId: string;   // Bot connection ID (used to derive chat session UID)
  streamKey?: string;     // Redis stream key (default: "transcription_segments")
}

/**
 * MeetingChatService
 *
 * Platform-abstracted interface for reading and writing meeting chat messages.
 * Supports Google Meet and Microsoft Teams via Playwright page automation.
 *
 * Chat messages are:
 * 1. Captured via MutationObserver in the browser
 * 2. Forwarded to Node.js via exposed function
 * 3. Published to Redis for upstream consumption
 * 4. Optionally injected into the transcription stream (appears in transcript)
 */
export class MeetingChatService {
  private page: Page;
  private platform: string;
  private meetingId: number;
  private botName: string;
  private messages: ChatMessage[] = [];
  private messageCallback: ((msg: ChatMessage) => void) | null = null;
  private observerInitialized: boolean = false;
  private redisPublisher: RedisClientType | null = null;

  // Track recently sent messages to identify bot's own messages in the observer
  // (bot's own messages appear with sender "Unknown" / "You" on its own browser side)
  private recentlySentTexts: Set<string> = new Set();

  // Transcript stream injection
  private transcriptConfig: ChatTranscriptConfig | null = null;
  private chatSessionUid: string | null = null;
  private sessionStartPublished: boolean = false;
  private sessionStartTimeMs: number;
  private chatSegmentCounter: number = 0;

  constructor(page: Page, platform: string, meetingId: number, botName: string, redisUrl?: string, transcriptConfig?: ChatTranscriptConfig) {
    this.page = page;
    this.platform = platform;
    this.meetingId = meetingId;
    this.botName = botName;
    this.sessionStartTimeMs = Date.now();

    // Set up transcript stream injection
    if (transcriptConfig) {
      this.transcriptConfig = transcriptConfig;
      this.chatSessionUid = `chat-${transcriptConfig.connectionId}`;
      log(`[Chat] Transcript injection enabled (session UID: ${this.chatSessionUid})`);
    }

    // Set up Redis publisher for chat events
    if (redisUrl) {
      this.initRedis(redisUrl).catch(err => {
        log(`[Chat] Redis init failed: ${err.message}`);
      });
    }
  }

  private async initRedis(url: string): Promise<void> {
    this.redisPublisher = createClient({ url }) as RedisClientType;
    this.redisPublisher.on('error', (err) => log(`[Chat] Redis error: ${err}`));
    await this.redisPublisher.connect();
    log('[Chat] Redis publisher connected');
  }

  /**
   * Send a message to the meeting chat.
   */
  async sendMessage(text: string): Promise<boolean> {
    try {
      // Track the sent text so the observer can identify bot's own messages
      // (on the bot's own browser, its messages appear without a sender header)
      this.recentlySentTexts.add(text);
      // Clean up after 30s to avoid unbounded growth
      setTimeout(() => this.recentlySentTexts.delete(text), 30000);

      if (this.platform === 'google_meet') {
        return await this.sendGoogleMeetChat(text);
      } else if (this.platform === 'teams') {
        return await this.sendTeamsChat(text);
      } else {
        log(`[Chat] Unsupported platform: ${this.platform}`);
        return false;
      }
    } catch (err: any) {
      log(`[Chat] Send failed: ${err.message}`);
      return false;
    }
  }

  /**
   * Start observing chat messages in the meeting.
   * Must be called after the bot has joined and the meeting UI is loaded.
   */
  async startChatObserver(): Promise<void> {
    if (this.observerInitialized) return;

    // Expose a function for the browser to call when new messages are detected
    try {
      await this.page.exposeFunction('__vexaChatMessage', (msg: ChatMessage) => {
        this.onNewMessage(msg);
      });
    } catch {
      // May already be exposed (e.g., if startChatObserver called twice)
    }

    // Expose a logging function for the browser observer to log back to Node
    try {
      await this.page.exposeFunction('__vexaChatLog', (message: string) => {
        log(`[Chat] ${message}`);
      });
    } catch {
      // May already be exposed
    }

    if (this.platform === 'google_meet') {
      await this.initGoogleMeetObserver();
    } else if (this.platform === 'teams') {
      await this.initTeamsObserver();
    }

    this.observerInitialized = true;
    log(`[Chat] Observer started for ${this.platform}`);
  }

  /**
   * Get all captured chat messages.
   */
  getChatMessages(): ChatMessage[] {
    return [...this.messages];
  }

  /**
   * Register callback for new chat messages.
   */
  onMessage(callback: (msg: ChatMessage) => void): void {
    this.messageCallback = callback;
  }

  /**
   * Handle a new message from the browser observer.
   */
  private onNewMessage(msg: ChatMessage): void {
    // Improve bot message detection: if the observer couldn't identify the sender
    // but we recently sent this exact text, it's the bot's own message
    if (!msg.isFromBot && this.recentlySentTexts.has(msg.text)) {
      msg.isFromBot = true;
      msg.sender = this.botName;
    }

    this.messages.push(msg);
    log(`[Chat] ${msg.isFromBot ? '→' : '←'} ${msg.sender}: ${msg.text.substring(0, 100)}`);

    // Publish to Redis pub/sub for real-time WebSocket delivery.
    // Uses the standard WebSocket envelope format so the API gateway
    // can forward it directly and the Dashboard can parse it uniformly.
    if (this.redisPublisher) {
      const wsEvent = JSON.stringify({
        type: 'chat.new_message',
        meeting: { id: this.meetingId },
        payload: {
          sender: msg.sender,
          text: msg.text,
          timestamp: msg.timestamp,
          is_from_bot: msg.isFromBot,
        },
        ts: new Date(msg.timestamp).toISOString(),
      });
      this.redisPublisher.publish(
        `va:meeting:${this.meetingId}:chat`,
        wsEvent
      ).catch(err => log(`[Chat] Redis publish failed: ${err.message}`));

      // Also store in Redis list for GET retrieval (use snake_case to match Dashboard types)
      this.redisPublisher.rPush(
        `meeting:${this.meetingId}:chat_messages`,
        JSON.stringify({
          sender: msg.sender,
          text: msg.text,
          timestamp: msg.timestamp,
          is_from_bot: msg.isFromBot,
        })
      ).catch(err => log(`[Chat] Redis store failed: ${err.message}`));
    }

    // Chat messages are rendered inline with the transcript in the Dashboard
    // via the dedicated chat panel + WS channel (no longer injected into transcript stream)
    if (false) {
    }

    if (this.messageCallback) {
      try { this.messageCallback(msg); } catch {}
    }
  }

  // ==================== Transcript Stream Injection ====================

  /**
   * Publish a session_start event for the chat session UID.
   * Must be called once before the first chat segment is published.
   */
  private async publishChatSessionStart(): Promise<void> {
    if (this.sessionStartPublished || !this.redisPublisher || !this.transcriptConfig || !this.chatSessionUid) return;

    const streamKey = this.transcriptConfig.streamKey || 'transcription_segments';
    const payload = JSON.stringify({
      type: 'session_start',
      token: this.transcriptConfig.token,
      platform: this.transcriptConfig.platform,
      meeting_id: this.transcriptConfig.meetingId,
      uid: this.chatSessionUid,
      start_timestamp: new Date().toISOString()
    });

    try {
      await this.redisPublisher.xAdd(streamKey, '*', { payload });
      this.sessionStartPublished = true;
      log(`[Chat] Published session_start for chat session UID: ${this.chatSessionUid}`);
    } catch (err: any) {
      log(`[Chat] Failed to publish session_start: ${err.message}`);
    }
  }

  /**
   * Publish a chat message as a transcription segment to the Redis stream.
   * This makes chat messages appear in the transcript alongside spoken words.
   *
   * Format: "[Chat] SenderName: message text"
   * The segment uses completed=true since chat messages are already finalized.
   */
  private async publishChatToTranscriptStream(msg: ChatMessage): Promise<void> {
    if (!this.redisPublisher || !this.transcriptConfig || !this.chatSessionUid) return;

    // Ensure session_start has been published first
    if (!this.sessionStartPublished) {
      await this.publishChatSessionStart();
    }

    const streamKey = this.transcriptConfig.streamKey || 'transcription_segments';

    // Calculate relative time from session start (in seconds, matching WhisperLive format)
    const relativeTimeSec = (msg.timestamp - this.sessionStartTimeMs) / 1000;
    // Use a small duration (0.5s) for chat messages — they're instant but need non-zero duration
    const segmentDuration = 0.5;
    this.chatSegmentCounter++;

    const chatText = `[Chat] ${msg.sender}: ${msg.text}`;

    const payload = JSON.stringify({
      type: 'transcription',
      token: this.transcriptConfig.token,
      platform: this.transcriptConfig.platform,
      meeting_id: this.transcriptConfig.meetingId,
      uid: this.chatSessionUid,
      segments: [{
        start: relativeTimeSec,
        end: relativeTimeSec + segmentDuration,
        text: chatText,
        language: 'en',
        completed: true
      }]
    });

    try {
      await this.redisPublisher.xAdd(streamKey, '*', { payload });
      log(`[Chat] Published chat to transcript stream: "${chatText.substring(0, 80)}..." (t=${relativeTimeSec.toFixed(1)}s)`);
    } catch (err: any) {
      log(`[Chat] Failed to publish to transcript stream: ${err.message}`);
    }
  }

  /**
   * Publish session_end for the chat session when cleaning up.
   */
  private async publishChatSessionEnd(): Promise<void> {
    if (!this.sessionStartPublished || !this.redisPublisher || !this.transcriptConfig || !this.chatSessionUid) return;

    const streamKey = this.transcriptConfig.streamKey || 'transcription_segments';
    const payload = JSON.stringify({
      type: 'session_end',
      token: this.transcriptConfig.token,
      platform: this.transcriptConfig.platform,
      meeting_id: this.transcriptConfig.meetingId,
      uid: this.chatSessionUid,
      end_timestamp: new Date().toISOString()
    });

    try {
      await this.redisPublisher.xAdd(streamKey, '*', { payload });
      log(`[Chat] Published session_end for chat session UID: ${this.chatSessionUid}`);
    } catch (err: any) {
      log(`[Chat] Failed to publish session_end: ${err.message}`);
    }
  }

  /**
   * Cleanup resources.
   */
  async cleanup(): Promise<void> {
    // Publish session_end for chat transcript session before disconnecting
    await this.publishChatSessionEnd();

    if (this.redisPublisher) {
      try { await this.redisPublisher.quit(); } catch {}
      this.redisPublisher = null;
    }
  }

  // ==================== Google Meet ====================

  private async sendGoogleMeetChat(text: string): Promise<boolean> {
    if (this.page.isClosed()) return false;

    return await this.page.evaluate(async (messageText: string) => {
      // Open chat panel if not already open
      const chatBtnSelectors = [
        'button[aria-label*="Chat with everyone"]',
        'button[aria-label*="chat"]',
        'button[aria-label*="Chat"]',
        'button[data-tooltip*="Chat"]'
      ];

      // Try to find and open chat
      for (const sel of chatBtnSelectors) {
        const btn = document.querySelector(sel) as HTMLElement | null;
        if (btn) {
          // Check if chat is already open by looking for the input
          const existing = document.querySelector('textarea[aria-label*="Send a message"]') ||
                          document.querySelector('textarea[aria-label*="chat"]') ||
                          document.querySelector('[contenteditable="true"][aria-label*="message"]');
          if (!existing) {
            btn.click();
            // Wait for chat panel to open
            await new Promise(r => setTimeout(r, 500));
          }
          break;
        }
      }

      // Find chat input
      const inputSelectors = [
        'textarea[aria-label*="Send a message"]',
        'textarea[aria-label*="chat"]',
        'textarea[aria-label*="Chat"]',
        '[contenteditable="true"][aria-label*="message"]',
        '[contenteditable="true"][aria-label*="Message"]'
      ];

      let input: HTMLElement | null = null;
      for (const sel of inputSelectors) {
        input = document.querySelector(sel) as HTMLElement | null;
        if (input) break;
      }

      if (!input) {
        (window as any).logBot?.('[Chat] Could not find chat input');
        return false;
      }

      // Focus and type
      input.focus();
      if (input.tagName === 'TEXTAREA') {
        (input as HTMLTextAreaElement).value = messageText;
        input.dispatchEvent(new Event('input', { bubbles: true }));
      } else {
        // ContentEditable
        input.textContent = messageText;
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }

      // Small delay then press Enter to send
      await new Promise(r => setTimeout(r, 100));

      // Try send button first
      const sendBtnSelectors = [
        'button[aria-label*="Send"]',
        'button[aria-label*="send"]',
        'button[data-tooltip*="Send"]'
      ];
      let sent = false;
      for (const sel of sendBtnSelectors) {
        const sendBtn = document.querySelector(sel) as HTMLElement | null;
        if (sendBtn && !sendBtn.hasAttribute('disabled')) {
          sendBtn.click();
          sent = true;
          break;
        }
      }

      if (!sent) {
        // Fallback: press Enter
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true }));
      }

      (window as any).logBot?.(`[Chat] Sent message: ${messageText.substring(0, 50)}`);
      return true;
    }, text);
  }

  private async initGoogleMeetObserver(): Promise<void> {
    const botName = this.botName;

    await this.page.evaluate((botNameArg: string) => {
      const observeChat = () => {
        // Track seen messages by data-message-id to avoid duplicates
        const seenMessages = new Set<string>();
        let chatPanelOpened = false;

        // Google Meet only renders chat message elements when the chat sidebar is visible.
        // We need to open the chat panel before we can scan for messages.
        const ensureChatPanelOpen = () => {
          if (chatPanelOpened) return;
          const chatBtnSelectors = [
            'button[aria-label*="Chat with everyone"]',
            'button[aria-label*="chat"]',
            'button[aria-label*="Chat"]',
            'button[data-tooltip*="Chat"]',
          ];
          for (const sel of chatBtnSelectors) {
            const btn = document.querySelector(sel) as HTMLElement | null;
            if (btn) {
              btn.click();
              chatPanelOpened = true;
              (window as any).__vexaChatLog?.(`Opened chat panel via: ${sel}`);
              break;
            }
          }
        };

        const scanForMessages = () => {
          // Try to open chat panel on each scan (idempotent — only clicks once)
          ensureChatPanelOpen();
          // Google Meet 2024-2026 DOM structure:
          //
          //   .aops0b  (message group container)
          //   ├── .HNucUd  (sender header — contains .poVWob with sender name)
          //   └── .beTDc   (message body wrapper)
          //       └── .RLrADb [data-message-id]  (individual message)
          //           └── .jO4O1  (message text, may contain pin overlay children)
          //
          // Pin overlay elements to strip:
          //   .UaaITe  — "Hover over a message to pin it" tooltip text
          //   .Sd72u   — pin button container
          //   .VYBDae-Bz112c-LgbsSe — pin button element (also has [data-message-id]!)
          //   .ne2Ple-oshW8e-V67aGc — pin related elements
          //
          const messageElements = document.querySelectorAll('[data-message-id]');

          messageElements.forEach((el) => {
            const msgId = el.getAttribute('data-message-id') || '';
            if (!msgId || seenMessages.has(msgId)) return;

            // Skip pin overlay buttons that also have [data-message-id]
            // Real messages have class .RLrADb; pin buttons have .VYBDae-Bz112c-LgbsSe
            if (el.classList.contains('VYBDae-Bz112c-LgbsSe') ||
                el.closest('.Sd72u')) {
              seenMessages.add(msgId);
              return;
            }

            // Extract message text from .jO4O1 (current GM class for message body)
            const textEl = el.querySelector('.jO4O1')
              || el.querySelector('.oIy2qc')          // legacy fallback
              || el.querySelector('[data-message-text]'); // legacy fallback

            if (!textEl) return;

            // Get clean text: clone the element and remove ALL pin/tooltip overlays
            const clone = textEl.cloneNode(true) as HTMLElement;
            clone.querySelectorAll('.Sd72u, .VYBDae-Bz112c-LgbsSe, .ne2Ple-oshW8e-V67aGc, .UaaITe').forEach(n => n.remove());
            const text = clone.textContent?.trim() || '';
            if (!text) return;

            // Extract sender name:
            // Walk up the parent chain from the message element. At each level,
            // check sibling elements for one containing .poVWob (sender name span).
            // In current Google Meet DOM, the sender header (.HNucUd containing .poVWob)
            // is a sibling of the message wrapper (.beTDc) inside the group (.aops0b).
            // This is typically found at depth 1 (grandparent level).
            let sender = 'Unknown';
            let currentNode: Element = el as Element;

            for (let depth = 0; depth < 6; depth++) {
              const parentEl = currentNode.parentElement;
              if (!parentEl) break;

              // Check all sibling elements at this level for .poVWob
              const siblings = Array.from(parentEl.children);
              for (let si = 0; si < siblings.length; si++) {
                const sib = siblings[si];
                if (sib === currentNode) continue;
                const senderEl = sib.querySelector('.poVWob');
                if (senderEl) {
                  sender = senderEl.textContent?.trim() || 'Unknown';
                  break;
                }
              }
              if (sender !== 'Unknown') break;

              // Also check previous siblings for legacy .zWGUib headers
              let prevSib: Element | null = currentNode.previousElementSibling;
              while (prevSib) {
                const senderEl = prevSib.querySelector('.poVWob')
                  || prevSib.querySelector('[data-sender-name]');
                if (senderEl) {
                  sender = senderEl.getAttribute('data-sender-name')
                    || senderEl.textContent?.trim()
                    || 'Unknown';
                  break;
                }
                prevSib = prevSib.previousElementSibling;
              }
              if (sender !== 'Unknown') break;

              currentNode = parentEl;
            }

            seenMessages.add(msgId);

            try {
              (window as any).__vexaChatMessage({
                sender,
                text,
                timestamp: Date.now(),
                isFromBot: sender === botNameArg
              });
            } catch {}
          });
        };

        // Set up MutationObserver on body for chat panel changes
        const observer = new MutationObserver(() => {
          scanForMessages();
        });
        observer.observe(document.body, {
          childList: true,
          subtree: true,
          characterData: true
        });

        // Initial scan
        scanForMessages();
        // Also poll periodically as backup
        setInterval(scanForMessages, 3000);
      };

      // Start observing after a short delay
      setTimeout(observeChat, 1000);
    }, botName);
  }

  // ==================== Microsoft Teams ====================

  private async sendTeamsChat(text: string): Promise<boolean> {
    if (this.page.isClosed()) return false;

    try {
      // Open chat panel if not open — use Playwright locator click
      const chatBtnSelectors = [
        'button[aria-label*="Chat"]:not([disabled])',
        '#chat-button',
        'button[data-tid*="chat"]'
      ];

      const inputSelectors = [
        '[contenteditable="true"][aria-label*="message"]',
        '[contenteditable="true"][aria-label*="Message"]',
        '[contenteditable="true"][data-tid*="message"]',
        'div[role="textbox"]',
        'textarea[aria-label*="Send a message"]',
        'textarea[aria-label*="Type a new message"]'
      ];

      // Check if chat input is already visible
      let inputLocator = this.page.locator(inputSelectors.join(', ')).first();
      let inputVisible = await inputLocator.isVisible().catch(() => false);

      if (!inputVisible) {
        // Try to open the chat panel
        for (const sel of chatBtnSelectors) {
          const btn = this.page.locator(sel).first();
          if (await btn.isVisible().catch(() => false)) {
            await btn.click();
            await this.page.waitForTimeout(1000);
            break;
          }
        }
        // Re-check for input
        inputLocator = this.page.locator(inputSelectors.join(', ')).first();
        inputVisible = await inputLocator.isVisible().catch(() => false);
      }

      if (!inputVisible) {
        log('[Chat] Could not find Teams chat input after opening chat panel');
        return false;
      }

      // Use Playwright's native click + type instead of DOM manipulation
      // This triggers the framework's event handlers properly
      await inputLocator.click();
      await this.page.waitForTimeout(100);

      // Type using keyboard — this fires real keydown/keypress/keyup/input events
      await this.page.keyboard.type(text, { delay: 10 });
      await this.page.waitForTimeout(200);

      // Send via Enter key (most reliable for Teams)
      await this.page.keyboard.press('Enter');

      log(`[Chat] Sent Teams message: ${text.substring(0, 50)}`);
      return true;
    } catch (err: any) {
      log(`[Chat] Failed to send Teams message: ${err.message}`);
      return false;
    }
  }

  private async initTeamsObserver(): Promise<void> {
    const botName = this.botName;
    await this.page.evaluate((botNameArg: string) => {
      const seenMessages = new Set<string>();

      const scanForMessages = () => {
        // Teams chat messages
        const messageSelectors = [
          '[data-tid*="chat-pane-message"]',
          '.message-body',
          '[data-tid*="messageBodyContent"]'
        ];

        for (const sel of messageSelectors) {
          document.querySelectorAll(sel).forEach((el) => {
            const text = el.textContent?.trim() || '';
            if (!text) return;

            // Generate a unique key from content + position
            const key = `${text}-${el.getBoundingClientRect().top}`;
            if (seenMessages.has(key)) return;
            seenMessages.add(key);

            // Try to find sender name
            let sender = 'Unknown';
            const parentMsg = el.closest('[data-tid*="chat-pane-message"]') || el.parentElement;
            if (parentMsg) {
              const senderEl = parentMsg.querySelector('[data-tid*="message-author"]') ||
                              parentMsg.querySelector('.ui-chat__messageheader__author');
              if (senderEl) sender = senderEl.textContent?.trim() || 'Unknown';
            }

            try {
              (window as any).__vexaChatMessage({
                sender,
                text,
                timestamp: Date.now(),
                isFromBot: sender === botNameArg
              });
            } catch {}
          });
        }
      };

      const observer = new MutationObserver(() => scanForMessages());
      observer.observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true
      });

      scanForMessages();
      setInterval(scanForMessages, 2000);
    }, botName);
  }
}

#!/usr/bin/env node
/**
 * Hot debug script: connects to the running bot browser via Playwright connectOverCDP.
 * Usage:
 *   node hot-debug.js <cdp_host_port> [command] [args...]
 * Commands:
 *   inspect              - inspect chat/speaker DOM state
 *   chat-send <text>     - send a chat message
 *   speaker              - check current speaker
 *   screenshot           - take screenshot to /tmp/bot-debug-screenshot.jpg
 *   eval <js>            - evaluate JS expression in page
 * Examples:
 *   node hot-debug.js 19217 inspect
 *   node hot-debug.js 19217 chat-send "Hello from hot debug!"
 *   node hot-debug.js 19217 speaker
 */

const { chromium } = require('playwright');

const PORT = process.argv[2] || '19217';
const CMD = process.argv[3] || 'inspect';
const ARGS = process.argv.slice(4);
const CDP_URL = `http://localhost:${PORT}`;

async function getZoomPage(browser) {
  for (const ctx of browser.contexts()) {
    for (const p of ctx.pages()) {
      if (p.url().includes('zoom.us') || p.url().includes('app.zoom')) {
        return p;
      }
    }
  }
  // fallback: first page
  const allPages = browser.contexts().flatMap(c => c.pages());
  return allPages[0] || null;
}

async function openChatPanel(page) {
  await page.evaluate(() => {
    const btn = document.querySelector('button[aria-label*="chat panel"]');
    if (btn) { btn.click(); return true; }
    return false;
  });
  await page.waitForTimeout(800);
}

async function main() {
  console.log(`[hot-debug] Connecting to ${CDP_URL} ...`);
  const browser = await chromium.connectOverCDP(CDP_URL);
  const page = await getZoomPage(browser);
  if (!page) { console.error('No page found'); await browser.close(); return; }
  console.log(`[hot-debug] Page: ${page.url().substring(0, 80)}`);

  switch (CMD) {

    case 'inspect': {
      await openChatPanel(page);
      const state = await page.evaluate(() => {
        const input = document.querySelector('.chat-rtf-box__editor-outer [contenteditable="true"]') ||
                      document.querySelector('.tiptap.ProseMirror');
        const sendBtn = document.querySelector('button[aria-label="send"]');
        const footer = document.querySelector('.speaker-active-container__video-frame .video-avatar__avatar-footer');
        const span = footer?.querySelector('span');
        const msgs = Array.from(document.querySelectorAll('.new-chat-message__container')).map(el => {
          const textEl = el.querySelector('.chat-rtf-box__display') || el.querySelector('.new-chat-message__content');
          return textEl?.textContent?.trim() || '';
        }).filter(Boolean);
        return {
          chatInputFound: !!input,
          chatInputVisible: !!input && input.offsetParent !== null,
          sendBtnFound: !!sendBtn,
          speakerName: span?.textContent?.trim() || footer?.innerText?.trim() || null,
          recentMessages: msgs.slice(-5),
          leaveButtonPresent: !!document.querySelector('button[aria-label="Leave"]'),
        };
      });
      console.log(JSON.stringify(state, null, 2));
      break;
    }

    case 'chat-send': {
      const text = ARGS.join(' ') || 'Hello from Vexa hot debug!';
      console.log(`[hot-debug] Sending chat: "${text}"`);
      await openChatPanel(page);

      // Find and click the input
      const input = page.locator('.chat-rtf-box__editor-outer [contenteditable="true"], .tiptap.ProseMirror').first();
      const inputVisible = await input.isVisible().catch(() => false);
      if (!inputVisible) {
        console.error('Chat input not visible after opening panel');
        break;
      }
      await input.click();
      await page.waitForTimeout(100);
      await page.keyboard.type(text, { delay: 20 });
      await page.waitForTimeout(200);

      // Click send button
      const sendBtn = page.locator('button[aria-label="send"], button[class*="chat-rtf-box__send"]').first();
      const sendVisible = await sendBtn.isVisible().catch(() => false);
      if (sendVisible) {
        await sendBtn.click();
        console.log('[hot-debug] Clicked send button');
      } else {
        await page.keyboard.press('Enter');
        console.log('[hot-debug] Pressed Enter to send');
      }
      await page.waitForTimeout(500);
      console.log('[hot-debug] Message sent');
      break;
    }

    case 'speaker': {
      const name = await page.evaluate(() => {
        const footer = document.querySelector('.speaker-active-container__video-frame .video-avatar__avatar-footer');
        const span = footer?.querySelector('span');
        return span?.textContent?.trim() || footer?.innerText?.trim() || null;
      });
      console.log(`Active speaker: ${name || '(none)'}`);
      break;
    }

    case 'screenshot': {
      const path = ARGS[0] || '/tmp/bot-debug-screenshot.jpg';
      await page.screenshot({ path, type: 'jpeg', quality: 70, fullPage: false });
      console.log(`Screenshot saved to ${path}`);
      break;
    }

    case 'eval': {
      const js = ARGS.join(' ');
      if (!js) { console.error('Usage: eval <js expression>'); break; }
      const result = await page.evaluate(js);
      console.log('Result:', JSON.stringify(result, null, 2));
      break;
    }

    default:
      console.error(`Unknown command: ${CMD}`);
      console.error('Commands: inspect, chat-send <text>, speaker, screenshot [path], eval <js>');
  }

  await browser.close();
}

main().catch(err => {
  console.error('[hot-debug] Error:', err.message);
  process.exit(1);
});

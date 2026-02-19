const WebSocket = require('ws');
const { execSync } = require('child_process');

const TAB_ID = '503049BBAE26FBAC54E87033B8FF9966';
const CDP_PORT = 19216;

function cdpCmd(method, params) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://localhost:${CDP_PORT}/devtools/page/${TAB_ID}`);
    ws.on('open', () => ws.send(JSON.stringify({id: 1, method, params: params || {}})));
    ws.on('message', (data) => { ws.close(); resolve(JSON.parse(data.toString())); });
    ws.on('error', reject);
    setTimeout(() => { ws.close(); reject(new Error('timeout ' + method)); }, 8000);
  });
}

function cdpEval(expression) {
  return cdpCmd('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function main() {
  // Click the chat button to open panel
  console.log('Opening chat panel...');
  await cdpEval(`
    (async () => {
      const btn = document.querySelector('button[aria-label*="chat panel"]');
      if (btn) { btn.click(); return 'clicked'; }
      return 'not found';
    })()
  `);
  
  await sleep(1500);
  
  // Now inspect the chat DOM
  const chatState = await cdpEval(`JSON.stringify({
    chatInputs: Array.from(document.querySelectorAll('[contenteditable]')).map(e => ({
      tag: e.tagName,
      visible: e.offsetParent !== null,
      ariaLabel: e.getAttribute('aria-label'),
      placeholder: e.getAttribute('data-placeholder') || e.getAttribute('placeholder'),
      className: e.className.substring(0,100),
      parentClass: e.parentElement?.className?.substring(0,100),
      grandparentClass: e.parentElement?.parentElement?.className?.substring(0,100),
      ggpClass: e.parentElement?.parentElement?.parentElement?.className?.substring(0,100),
    })),
    chatPanelOpen: !!document.querySelector('[class*="chat-panel"]'),
    chatBoxClasses: Array.from(document.querySelectorAll('[class*="chat-box"]')).map(e => ({cls: e.className.substring(0,100), tag: e.tagName, children: e.children.length})),
    chatInputClasses: Array.from(document.querySelectorAll('[class*="chat-input"]')).map(e => ({cls: e.className.substring(0,100), tag: e.tagName})),
    chatTextareaClasses: Array.from(document.querySelectorAll('[class*="chat-textarea"]')).map(e => ({cls: e.className.substring(0,100), tag: e.tagName, visible: e.offsetParent !== null})),
    chatMessageClasses: Array.from(document.querySelectorAll('[class*="chat-message"]')).slice(0,3).map(e => ({cls: e.className.substring(0,100), tag: e.tagName})),
  })`);
  
  console.log('=== CHAT DOM (after open) ===');
  console.log(JSON.stringify(JSON.parse(chatState.result?.result?.value || '{}'), null, 2));
}

main().catch(e => console.error('Fatal:', e.message));

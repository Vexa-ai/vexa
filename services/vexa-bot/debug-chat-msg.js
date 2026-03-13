const WebSocket = require('ws');

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

async function main() {
  const result = await cdpEval(`JSON.stringify({
    // All new-chat-message structure
    newChatMessages: Array.from(document.querySelectorAll('[class*="new-chat-message"]')).slice(0,5).map(e => ({
      cls: e.className.substring(0,100),
      text: e.textContent?.trim()?.substring(0,100),
      children: Array.from(e.children).map(c => ({tag: c.tagName, cls: c.className?.substring?.(0,80), text: c.textContent?.trim()?.substring(0,50)})),
    })),
    // Sender name elements
    senderEls: Array.from(document.querySelectorAll('[class*="sender"], [class*="Sender"], [class*="author"], [class*="Author"]')).slice(0,5).map(e => ({
      cls: e.className.substring(0,100),
      text: e.textContent?.trim()?.substring(0,50),
    })),
    // Chat panel structure
    chatPanelRoot: (() => {
      const el = document.querySelector('[class*="chat-panel"]') || document.querySelector('[class*="chatPanel"]');
      if (!el) return null;
      return {cls: el.className.substring(0,100), childCount: el.children.length};
    })(),
    // The send button
    sendBtn: Array.from(document.querySelectorAll('button')).filter(b => b.getAttribute('aria-label')?.toLowerCase().includes('send') || b.textContent?.trim()?.toLowerCase() === 'send').map(b => ({cls: b.className.substring(0,80), label: b.getAttribute('aria-label'), text: b.textContent?.trim()})),
  })`);
  
  console.log(JSON.stringify(JSON.parse(result.result?.result?.value || '{}'), null, 2));
}

main().catch(e => console.error('Fatal:', e.message));

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
  // 1. Chat DOM inspection
  const chatState = await cdpEval(`JSON.stringify({
    chatInputs: Array.from(document.querySelectorAll('[contenteditable]')).map(e => ({
      tag: e.tagName,
      visible: e.offsetParent !== null,
      ariaLabel: e.getAttribute('aria-label'),
      placeholder: e.getAttribute('data-placeholder') || e.getAttribute('placeholder'),
      className: e.className.substring(0,80),
      parentClass: e.parentElement?.className?.substring(0,80),
      grandparentClass: e.parentElement?.parentElement?.className?.substring(0,80),
      text: e.textContent?.substring(0,50)
    })),
    chatPanelOpen: !!document.querySelector('[class*="chat-panel"]:not([style*="display: none"])'),
    chatBoxClasses: Array.from(document.querySelectorAll('[class*="chat-box"]')).map(e => e.className.substring(0,80)),
    chatInputClasses: Array.from(document.querySelectorAll('[class*="chat-input"]')).map(e => ({cls: e.className.substring(0,80), tag: e.tagName})),
    chatTextareaClasses: Array.from(document.querySelectorAll('[class*="chat-textarea"]')).map(e => ({cls: e.className.substring(0,80), tag: e.tagName})),
    chatButton: document.querySelector('button[aria-label*="chat panel"]')?.getAttribute('aria-label'),
    leaveButton: !!document.querySelector('button[aria-label="Leave"]'),
  })`);
  console.log('=== CHAT DOM ===');
  console.log(JSON.stringify(JSON.parse(chatState.result?.result?.value || '{}'), null, 2));
  
  // 2. Speaker DOM inspection
  const speakerState = await cdpEval(`JSON.stringify({
    activeSpeakerContainer: !!document.querySelector('.speaker-active-container__video-frame'),
    speakerName: document.querySelector('.speaker-active-container__video-frame .video-avatar__avatar-name')?.textContent?.trim(),
    allAvatarNames: Array.from(document.querySelectorAll('.video-avatar__avatar-name')).map(e => e.textContent?.trim()),
    videoTiles: document.querySelectorAll('.video-avatar__avatar').length,
    speakerBarFrames: document.querySelectorAll('.speaker-bar-container__video-frame').length,
    videoAvatarParentClasses: Array.from(document.querySelectorAll('.video-avatar__avatar')).slice(0,3).map(e => e.parentElement?.className?.substring(0,80)),
  })`);
  console.log('\n=== SPEAKER DOM ===');
  console.log(JSON.stringify(JSON.parse(speakerState.result?.result?.value || '{}'), null, 2));
}

main().catch(e => console.error('Fatal:', e.message));

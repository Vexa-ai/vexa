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
    // Check speaker bar active
    speakerBarActive: Array.from(document.querySelectorAll('[class*="speaker-bar-container__video-frame"]')).map(e => ({
      cls: e.className.substring(0,120),
      nameText: e.querySelector('.video-avatar__avatar-name')?.textContent?.trim(),
      isActive: e.className.includes('--active'),
    })),
    // Check active speaker container
    activeSpeakerFrame: (() => {
      const el = document.querySelector('.speaker-active-container__video-frame');
      if (!el) return null;
      return {
        cls: el.className,
        nameEl: !!el.querySelector('.video-avatar__avatar-name'),
        nameText: el.querySelector('.video-avatar__avatar-name')?.textContent?.trim(),
        innerText: el.innerText?.substring(0,100),
        allText: el.textContent?.substring(0,100),
        allChildren: Array.from(el.querySelectorAll('*')).map(c => ({tag: c.tagName, cls: c.className?.substring?.(0,50)})).slice(0,10),
      };
    })(),
    // All video frames
    allVideoFrames: Array.from(document.querySelectorAll('[class*="video-frame"]')).map(e => ({
      cls: e.className.substring(0,100),
      nameText: e.querySelector('.video-avatar__avatar-name')?.textContent?.trim(),
    })).slice(0,10),
  })`);
  
  console.log(JSON.stringify(JSON.parse(result.result?.result?.value || '{}'), null, 2));
}

main().catch(e => console.error('Fatal:', e.message));

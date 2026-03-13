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
    // Detailed active speaker
    activeSpeakerFooter: (() => {
      const footer = document.querySelector('.speaker-active-container__video-frame .video-avatar__avatar-footer');
      if (!footer) return null;
      return {
        cls: footer.className,
        innerText: footer.innerText?.substring(0,100),
        children: Array.from(footer.querySelectorAll('*')).map(c => ({
          tag: c.tagName,
          cls: c.className?.substring?.(0,80),
          text: c.textContent?.trim()?.substring(0,50),
        })).slice(0,5),
      };
    })(),
    // Check avatar name in bar frames
    barFrameDetails: Array.from(document.querySelectorAll('.speaker-bar-container__video-frame')).map(el => ({
      cls: el.className.substring(0,100),
      footer: el.querySelector('.video-avatar__avatar-footer')?.innerText?.substring(0,50),
      footerCls: el.querySelector('.video-avatar__avatar-footer')?.className?.substring(0,80),
      allNames: Array.from(el.querySelectorAll('[class*="name"]')).map(e => ({cls: e.className?.substring(0,50), text: e.textContent?.trim()?.substring(0,30)})),
    })),
    // Check for any element with participant name
    nameElements: Array.from(document.querySelectorAll('[class*="name"], [class*="Name"]')).filter(e => e.offsetParent !== null).map(e => ({
      cls: e.className.substring(0,80),
      text: e.textContent?.trim()?.substring(0,50),
      tag: e.tagName,
    })).slice(0,10),
  })`);
  
  console.log(JSON.stringify(JSON.parse(result.result?.result?.value || '{}'), null, 2));
}

main().catch(e => console.error('Fatal:', e.message));

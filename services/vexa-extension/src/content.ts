/**
 * Content script (isolated world).
 *
 * Bridges the MAIN-world capture (inpage.ts) and the background service worker:
 *   - injects inpage.js into the page's MAIN world
 *   - relays captured audio / control messages from inpage → background
 *   - forwards START/STOP commands from background → inpage
 *
 * No networking happens here; the background worker owns the WebSocket so it is
 * governed by the extension's host permissions, not Google Meet's page CSP.
 */

const VEXA = '[vexa-content]';

function injectInpage(): void {
  if (document.getElementById('vexa-inpage-script')) return;
  const s = document.createElement('script');
  s.id = 'vexa-inpage-script';
  s.src = chrome.runtime.getURL('inpage.js');
  s.onload = () => s.remove();
  (document.head || document.documentElement).appendChild(s);
}

function sendToInpage(command: string): void {
  window.postMessage({ __vexaControl: true, command }, '*');
}

// Relay messages coming up from the MAIN-world capture loop.
window.addEventListener('message', (event) => {
  if (event.source !== window) return;
  const data = event.data;
  if (!data || data.__vexa !== true) return;

  switch (data.type) {
    case 'audio':
      // Forward one per-speaker PCM chunk to the background WebSocket.
      chrome.runtime.sendMessage({ type: 'audio', index: data.index, pcm: data.pcm });
      break;
    case 'capture-started':
    case 'capture-stopped':
    case 'inpage-ready':
      chrome.runtime.sendMessage({ type: data.type, streams: data.streams });
      break;
  }
});

// Commands from the popup → background → content → inpage.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'BEGIN_CAPTURE') {
    sendToInpage('vexa-start');
    sendResponse({ ok: true });
  } else if (msg.type === 'END_CAPTURE') {
    sendToInpage('vexa-stop');
    sendResponse({ ok: true });
  }
  return true;
});

injectInpage();
console.log(`${VEXA} ready on ${location.href}`);

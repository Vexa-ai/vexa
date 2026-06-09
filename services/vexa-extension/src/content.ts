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

// The capture loop (inpage.ts) is registered as a MAIN-world content script in
// the manifest, so Chrome injects it directly (bypassing Google Meet's page CSP
// that would block a <script src> injection). No manual injection needed here.

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
    case 'speakers':
      chrome.runtime.sendMessage({ type: 'speakers', speakers: data.speakers });
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

// --- Auto-start: when this tab is in an actual Meet meeting, ask the
// background to start capturing. Meet is a SPA, so the URL can change from the
// landing page to a meeting code without a reload — poll for that transition.
function isMeetingUrl(): boolean {
  const seg = location.pathname.split('/').filter(Boolean)[0];
  return !!seg && /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/i.test(seg);
}

let lastSeenUrl = '';
function checkAutoStart(): void {
  if (location.href === lastSeenUrl) return;
  lastSeenUrl = location.href;
  if (isMeetingUrl()) {
    // Give the meeting UI / media a moment to come up, then request auto-start.
    setTimeout(() => chrome.runtime.sendMessage({ type: 'AUTO_START' }).catch(() => { /* sw asleep */ }), 1500);
  }
}
checkAutoStart();
setInterval(checkAutoStart, 2000);

console.log(`${VEXA} ready on ${location.href}`);

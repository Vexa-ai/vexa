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

import { detectMeeting, isTeamsHost, TEAMS_IN_MEETING_SELECTORS } from './meeting';

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
    case 'diag':
      chrome.runtime.sendMessage({ type: 'diag', diag: data.diag });
      break;
    case 'dom_probe':
      chrome.runtime.sendMessage({ type: 'dom_probe', probe: data.probe });
      break;
    case 'speaker_activity':
      chrome.runtime.sendMessage({ type: 'speaker_activity', name: data.name, kind: data.kind, isEnd: data.isEnd });
      break;
    case 'chat-message':
      chrome.runtime.sendMessage({ type: 'chat-message', sender: data.sender, text: data.text });
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

// --- Auto-start: when this tab is in an actual meeting (Google Meet or Zoom
// Web), ask the background to start capturing. These are SPAs, so the URL can
// change from a landing page to a meeting without a reload — poll for that.
let lastSeenUrl = '';
function checkAutoStart(): void {
  if (location.href === lastSeenUrl) return;
  lastSeenUrl = location.href;
  if (detectMeeting(location.href)) {
    // Capture the URL now — SPAs (especially Teams) can navigate away from the
    // /meet/<id> path after joining, losing the meeting id. Then give the
    // meeting UI / media a moment to come up before requesting auto-start.
    const detectedUrl = location.href;
    setTimeout(() => chrome.runtime.sendMessage({ type: 'AUTO_START', url: detectedUrl }).catch(() => { /* sw asleep */ }), 1500);
  }
}
checkAutoStart();
setInterval(checkAutoStart, 2000);

// --- Teams app flow (teams.cloud.microsoft / the /v2/ SPA): the URL never
// carries a meeting id, so detect "in a meeting" from the DOM — the same
// hangup-button selectors the bot's admission check uses — and synthesize a
// per-meeting native id. Also auto-STOP when the call ends, otherwise the mic
// would keep streaming while the user sits in Teams chat.
const TEAMS_ID_KEY = '__vexaTeamsNativeId';
let teamsInMeeting = false;

function checkTeamsDom(): void {
  if (!isTeamsHost(location.hostname)) return;
  if (detectMeeting(location.href)) return; // URL carries the id — URL flow owns it
  const inMeeting = TEAMS_IN_MEETING_SELECTORS.some(s => !!document.querySelector(s));

  if (inMeeting && !teamsInMeeting) {
    let nativeId = sessionStorage.getItem(TEAMS_ID_KEY);
    if (!nativeId) {
      nativeId = `cloud-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
      sessionStorage.setItem(TEAMS_ID_KEY, nativeId);
    }
    console.log(`${VEXA} Teams meeting detected (DOM), native id ${nativeId}`);
    chrome.runtime.sendMessage({
      type: 'AUTO_START',
      meeting: { platform: 'teams', nativeMeetingId: nativeId },
    }).catch(() => { /* sw asleep */ });
  } else if (!inMeeting && teamsInMeeting) {
    // Call ended — stop capture and drop the id so the next call in this tab
    // becomes a fresh meeting.
    sessionStorage.removeItem(TEAMS_ID_KEY);
    console.log(`${VEXA} Teams meeting ended (DOM), stopping capture`);
    chrome.runtime.sendMessage({ type: 'AUTO_STOP' }).catch(() => { /* sw asleep */ });
  }
  teamsInMeeting = inMeeting;
}
setInterval(checkTeamsDom, 2000);

console.log(`${VEXA} ready on ${location.href}`);

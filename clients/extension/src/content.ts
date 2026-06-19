/**
 * Content script (isolated world) — GOOGLE MEET ONLY.
 *
 * Bridges the MAIN-world capture (inpage.ts) and the background service worker:
 *   - relays captured audio / control messages from inpage → background
 *   - forwards START/STOP commands from background → inpage
 *
 * No networking happens here; the background worker owns the WebSocket so it is
 * governed by the extension's host permissions, not Google Meet's page CSP.
 */

import { detectMeeting } from './meeting';

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
      // speakerName = the glow name bound at the source (gmeet); undefined otherwise.
      chrome.runtime.sendMessage({ type: 'audio', index: data.index, pcm: data.pcm, speakerName: data.speakerName });
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
  }
});

// Commands from the panel → background → content → inpage.
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

// --- Auto-start: when this tab is in an actual Google Meet, ask the background
// to start capturing. Meet is an SPA, so the URL can change from a landing page
// to a meeting without a reload — poll for that.
let lastSeenUrl = '';
function checkAutoStart(): void {
  if (location.href === lastSeenUrl) return;
  lastSeenUrl = location.href;
  if (detectMeeting(location.href)) {
    const detectedUrl = location.href;
    // Give the meeting UI / media a moment to come up before requesting auto-start.
    setTimeout(() => chrome.runtime.sendMessage({ type: 'AUTO_START', url: detectedUrl }).catch(() => { /* sw asleep */ }), 1500);
  }
}
checkAutoStart();
setInterval(checkAutoStart, 2000);

// Teams' new web app (teams.cloud.microsoft) + meetings created in-tab NEVER expose
// the meeting id in the URL. Read the canonical thread id (19:meeting_…@thread.v2)
// from the page (DOM + same-origin storage) and report it so the background can key
// the session. Scan until found (stable per meeting), then stop.
function isTeamsHost(): boolean {
  return location.hostname.endsWith('teams.microsoft.com')
    || location.hostname.endsWith('teams.live.com')
    || location.hostname === 'teams.cloud.microsoft';
}
let teamsReported = false;
function checkTeamsMeeting(): void {
  if (teamsReported || !isTeamsHost()) return;
  let blob = '';
  try { blob = document.documentElement.outerHTML; } catch { /* */ }
  try { blob += ' ' + JSON.stringify({ ...sessionStorage, ...localStorage }); } catch { /* */ }
  const m = blob.match(/19:meeting_[A-Za-z0-9_\-]+@thread\.v2/);
  if (m) {
    teamsReported = true;
    chrome.runtime.sendMessage({ type: 'MEETING_HINT', meeting: { platform: 'teams', nativeMeetingId: m[0] } }).catch(() => { /* sw asleep */ });
    console.log(`${VEXA} teams meeting id (from DOM): ${m[0]}`);
  }
}
checkTeamsMeeting();
setInterval(checkTeamsMeeting, 2000);

console.log(`${VEXA} ready on ${location.href}`);

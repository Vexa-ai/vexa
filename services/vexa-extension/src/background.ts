/**
 * Background service worker.
 *
 * Owns the single WebSocket to the ingest server (the bot's Node pipeline).
 * Because it runs in the extension's own context with host permissions, its
 * WebSocket is NOT subject to Google Meet's page CSP. It receives per-speaker
 * PCM chunks from the content script, packs each into a binary frame
 * ([Int32LE speakerIndex][Float32LE pcm…]) and streams it to the ingest server.
 *
 * Control flow: popup → START/STOP here → open/close WS and tell the meeting
 * tab's content script to begin/end capture.
 */

import { detectMeeting, MeetingRef } from './meeting';

interface SessionState {
  status: 'idle' | 'connecting' | 'capturing' | 'error';
  tabId: number | null;
  meetingId: number | null;
  platform: string | null;
  nativeMeetingId: string | null;
  streams: number;
  error: string | null;
  /** Mixed remote-audio capture state (Zoom/Teams): none | pending | on | error:<msg> */
  tabAudio: string;
}

const state: SessionState = {
  status: 'idle',
  tabId: null,
  meetingId: null,
  platform: null,
  nativeMeetingId: null,
  streams: 0,
  error: null,
  tabAudio: 'none',
};

/** tabCapture stream id (minted in the panel on the Start gesture) for Zoom/Teams. */
let tabStreamId: string | null = null;

let ws: WebSocket | null = null;

function broadcastStatus(): void {
  chrome.runtime.sendMessage({ type: 'STATUS', state }).catch(() => { /* popup may be closed */ });
}

/** Ensure the offscreen mic-capture document exists (voice-notepad mode). */
async function ensureOffscreen(): Promise<void> {
  const has = await (chrome.offscreen as any).hasDocument?.().catch(() => false);
  if (has) return;
  await chrome.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: [chrome.offscreen.Reason.USER_MEDIA],
    justification: 'Microphone capture for voice notes',
  }).catch((e) => { if (!String(e).includes('single offscreen')) throw e; });
}

/** Start (or attach) the mixed tab-audio capture for the current session. */
function startTabAudio(): void {
  if (!tabStreamId) {
    state.tabAudio = 'error:tab audio needs a click — use "Enable remote audio" in the panel';
    broadcastStatus();
    return;
  }
  state.tabAudio = 'pending'; broadcastStatus();
  ensureOffscreen()
    .then(() => chrome.runtime.sendMessage({ type: 'TAB_CAPTURE_START', streamId: tabStreamId }))
    .then((res: any) => {
      state.tabAudio = (res && res.ok) ? 'on' : `error:${res?.error || 'no response from offscreen'}`;
      broadcastStatus();
    })
    .catch((e) => { state.tabAudio = `error:${e.message}`; broadcastStatus(); });
}

async function startCaptureForTab(tabId: number, url: string, meetingRef?: MeetingRef): Promise<void> {
  if (state.status === 'capturing' || state.status === 'connecting') return;

  // An explicit ref wins — the Teams app flow detects the meeting from the DOM
  // and synthesizes the id, because its URL carries none.
  // No meeting anywhere → voice-notepad mode: capture the mic via the
  // offscreen document under the synthetic 'note' platform.
  const meeting = meetingRef || detectMeeting(url)
    || { platform: 'note' as any, nativeMeetingId: `note-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}` };

  const cfg = await chrome.storage.local.get(['apiKey', 'ingestUrl', 'language']);
  const apiKey: string = cfg.apiKey || '';
  const ingestUrl: string = cfg.ingestUrl || 'ws://localhost:8092/ingest';
  const language: string = cfg.language || 'auto';

  state.status = 'connecting';
  state.tabId = tabId;
  state.platform = meeting.platform;
  state.nativeMeetingId = meeting.nativeMeetingId;
  state.error = null;
  broadcastStatus();

  const qs = new URLSearchParams({
    platform: meeting.platform,
    native_meeting_id: meeting.nativeMeetingId,
    api_key: apiKey,
    language,
  });
  const fullUrl = `${ingestUrl}?${qs.toString()}`;

  try {
    ws = new WebSocket(fullUrl);
  } catch (err: any) {
    state.status = 'error'; state.error = `WS open failed: ${err.message}`; broadcastStatus(); return;
  }
  ws.binaryType = 'arraybuffer';

  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
      if (msg.type === 'ready') {
        state.meetingId = msg.meeting_id;
        state.status = 'capturing';
        broadcastStatus();
        if (state.platform === 'note') {
          ensureOffscreen()
            .then(() => chrome.runtime.sendMessage({ type: 'NOTE_CAPTURE_START' }))
            .then((res: any) => {
              if (res && res.ok === false) {
                state.status = 'error';
                state.error = res.error === 'NotAllowedError' ? 'mic-permission' : `Mic capture failed: ${res.error}`;
                broadcastStatus();
                stopCapture();
              }
            })
            .catch((e) => { state.status = 'error'; state.error = `Offscreen failed: ${e.message}`; broadcastStatus(); });
        } else {
          // Page-side capture: local mic ("You") everywhere; per-participant
          // <audio> elements on Google Meet.
          if (state.tabId !== null) {
            chrome.tabs.sendMessage(state.tabId, { type: 'BEGIN_CAPTURE' }).catch(() => { /* content not ready */ });
          }
          // Zoom/Teams don't expose per-participant audio to the DOM — capture
          // the tab's mixed output (all remote participants) via tabCapture.
          if (state.platform === 'zoom' || state.platform === 'teams') {
            startTabAudio();
          }
        }
      } else if (msg.type === 'error') {
        state.status = 'error'; state.error = msg.message; broadcastStatus();
      }
    } catch { /* non-JSON frame; ignore */ }
  };

  ws.onerror = () => {
    state.status = 'error'; state.error = 'WebSocket error'; broadcastStatus();
  };

  ws.onclose = () => {
    if (state.status !== 'error') { state.status = 'idle'; }
    state.meetingId = null;
    broadcastStatus();
  };
}

/** Manual start from the popup — targets the active tab. */
async function startCaptureActiveTab(): Promise<void> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id || !tab.url) {
    state.status = 'error'; state.error = 'No active tab'; broadcastStatus(); return;
  }
  return startCaptureForTab(tab.id, tab.url);
}

/** Auto-start when a meeting tab reports it's in a meeting (if enabled + configured). */
async function maybeAutoStart(tabId?: number, url?: string, meetingRef?: MeetingRef): Promise<void> {
  if (!tabId || !url) return;
  if (state.status === 'capturing' || state.status === 'connecting') return;
  const cfg = await chrome.storage.local.get(['autoStart', 'apiKey']);
  if (cfg.autoStart === false) return;          // default ON
  if (!cfg.apiKey) return;                       // need an API key configured first
  if (!meetingRef && !detectMeeting(url)) return;
  startCaptureForTab(tabId, url, meetingRef);
}

async function stopCapture(): Promise<void> {
  if (state.platform === 'note') {
    chrome.runtime.sendMessage({ type: 'NOTE_CAPTURE_STOP' }).catch(() => { /* offscreen gone */ });
  } else {
    if (state.tabId !== null) {
      chrome.tabs.sendMessage(state.tabId, { type: 'END_CAPTURE' }).catch(() => { /* tab gone */ });
    }
    if (state.platform === 'zoom' || state.platform === 'teams') {
      chrome.runtime.sendMessage({ type: 'TAB_CAPTURE_STOP' }).catch(() => { /* offscreen gone */ });
    }
  }
  tabStreamId = null;
  if (ws) { try { ws.close(); } catch { /* ignore */ } ws = null; }
  state.status = 'idle';
  state.meetingId = null;
  state.streams = 0;
  state.tabAudio = 'none';
  broadcastStatus();
}

// Pack and forward one per-speaker PCM chunk.
function sendAudio(index: number, pcm: number[]): void {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const samples = Float32Array.from(pcm);
  const buf = new ArrayBuffer(4 + samples.byteLength);
  new DataView(buf).setInt32(0, index, true);
  new Float32Array(buf, 4).set(samples);
  ws.send(buf);
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.type) {
    case 'START':
      tabStreamId = msg.streamId || null; // tab audio stream id minted in the panel (Zoom/Teams)
      startCaptureActiveTab(); sendResponse({ ok: true }); break;
    case 'ATTACH_TAB_AUDIO':
      // Mid-session attach (auto-start sessions have no gesture, so no stream
      // id — the panel mints one on the "Enable remote audio" click).
      tabStreamId = msg.streamId || null;
      if (state.status === 'capturing' && (state.platform === 'zoom' || state.platform === 'teams')) startTabAudio();
      sendResponse({ ok: true }); break;
    case 'AUTO_START':
      // Prefer the URL the content script saw at detection time — SPAs (Teams)
      // can navigate off the /meet/<id> path by the time this message arrives.
      maybeAutoStart(sender.tab?.id, msg.url || sender.tab?.url, msg.meeting); sendResponse({ ok: true }); break;
    case 'AUTO_STOP':
      // Teams app flow: the call ended (hangup button gone) — stop so the mic
      // doesn't keep streaming while the user sits in Teams chat.
      if (sender.tab?.id === state.tabId) stopCapture();
      sendResponse({ ok: true }); break;
    case 'STOP':
      stopCapture(); sendResponse({ ok: true }); break;
    case 'STATUS':
      sendResponse({ state }); break;
    case 'PREFLIGHT':
      // Tell the panel what Start would do, so it can pre-grant mic permission
      // for note mode (offscreen documents can't show permission prompts).
      chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
        sendResponse({ mode: tab?.url && detectMeeting(tab.url) ? 'meeting' : 'note' });
      }).catch(() => sendResponse({ mode: 'note' }));
      return true;
    case 'audio':
      sendAudio(msg.index, msg.pcm); break;
    case 'speakers':
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'speakers', speakers: msg.speakers }));
      break;
    case 'capture-started':
      state.streams = msg.streams || 0; broadcastStatus(); break;
    case 'capture-stopped':
      break;
  }
  return true;
});

// If the captured tab closes, tear down.
chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === state.tabId) stopCapture();
});

// Toolbar click opens the side panel (replaces the old popup).
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => { /* older Chrome */ });

// Dev auto-reload: build.mjs rewrites build-stamp.txt on every build; when the
// on-disk stamp changes (e.g. dist/ is an SSHFS/rsync mirror of a remote build),
// reload the extension from disk. Cheap local-resource fetch; inert for any
// packaged build where the stamp never changes.
const stampUrl = chrome.runtime.getURL('build-stamp.txt');
let knownStamp: string | null = null;
fetch(stampUrl).then(r => r.text()).then(s => { knownStamp = s; }).catch(() => { /* no stamp */ });
setInterval(async () => {
  try {
    const cur = await fetch(stampUrl, { cache: 'no-store' }).then(r => r.text());
    if (knownStamp && cur && cur !== knownStamp) {
      // Never hot-reload mid-capture — reload() restarts capture, churns the
      // ingest session, and resets the transcription buffer. Defer until idle.
      if (state.status === 'capturing' || state.status === 'connecting') return;
      chrome.runtime.reload();
    }
  } catch { /* stamp unreadable; skip */ }
}, 2000);

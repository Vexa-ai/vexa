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

interface SessionState {
  status: 'idle' | 'connecting' | 'capturing' | 'error';
  tabId: number | null;
  meetingId: number | null;
  nativeMeetingId: string | null;
  streams: number;
  error: string | null;
}

const state: SessionState = {
  status: 'idle',
  tabId: null,
  meetingId: null,
  nativeMeetingId: null,
  streams: 0,
  error: null,
};

let ws: WebSocket | null = null;

function broadcastStatus(): void {
  chrome.runtime.sendMessage({ type: 'STATUS', state }).catch(() => { /* popup may be closed */ });
}

/** Extract the native meeting id from a Google Meet tab URL. */
function parseNativeMeetingId(url: string): string | null {
  try {
    const u = new URL(url);
    if (!u.hostname.endsWith('meet.google.com')) return null;
    const seg = u.pathname.split('/').filter(Boolean)[0];
    // Meet codes look like abc-defg-hij; reject landing/other paths.
    if (seg && /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/i.test(seg)) return seg;
    return seg || null;
  } catch {
    return null;
  }
}

async function startCaptureForTab(tabId: number, url: string): Promise<void> {
  if (state.status === 'capturing' || state.status === 'connecting') return;

  const nativeMeetingId = parseNativeMeetingId(url);
  if (!nativeMeetingId) {
    state.status = 'error'; state.error = 'Not a Google Meet meeting'; broadcastStatus(); return;
  }

  const cfg = await chrome.storage.local.get(['apiKey', 'ingestUrl', 'language']);
  const apiKey: string = cfg.apiKey || '';
  const ingestUrl: string = cfg.ingestUrl || 'ws://localhost:8092/ingest';
  const language: string = cfg.language || 'auto';

  state.status = 'connecting';
  state.tabId = tabId;
  state.nativeMeetingId = nativeMeetingId;
  state.error = null;
  broadcastStatus();

  const qs = new URLSearchParams({
    platform: 'google_meet',
    native_meeting_id: nativeMeetingId,
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
        if (state.tabId !== null) {
          chrome.tabs.sendMessage(state.tabId, { type: 'BEGIN_CAPTURE' }).catch(() => { /* content not ready */ });
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

/** Auto-start when a Meet tab reports it's in a meeting (if enabled + configured). */
async function maybeAutoStart(tabId?: number, url?: string): Promise<void> {
  if (!tabId || !url) return;
  if (state.status === 'capturing' || state.status === 'connecting') return;
  const cfg = await chrome.storage.local.get(['autoStart', 'apiKey']);
  if (cfg.autoStart === false) return;          // default ON
  if (!cfg.apiKey) return;                       // need an API key configured first
  if (!parseNativeMeetingId(url)) return;
  startCaptureForTab(tabId, url);
}

async function stopCapture(): Promise<void> {
  if (state.tabId !== null) {
    chrome.tabs.sendMessage(state.tabId, { type: 'END_CAPTURE' }).catch(() => { /* tab gone */ });
  }
  if (ws) { try { ws.close(); } catch { /* ignore */ } ws = null; }
  state.status = 'idle';
  state.meetingId = null;
  state.streams = 0;
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
      startCaptureActiveTab(); sendResponse({ ok: true }); break;
    case 'AUTO_START':
      maybeAutoStart(sender.tab?.id, sender.tab?.url); sendResponse({ ok: true }); break;
    case 'STOP':
      stopCapture(); sendResponse({ ok: true }); break;
    case 'STATUS':
      sendResponse({ state }); break;
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

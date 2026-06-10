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
  /** build-stamp.txt as read when THIS service worker loaded. The panel compares
   *  it to the on-disk stamp to detect a stale SW (reload deferred during capture). */
  swBuild: string;
  /** Newer build sitting on disk while reload is deferred (set by the stamp
   *  watcher). Lets the panel show "Reload now" even when the panel itself is
   *  equally stale (panelBuild == swBuild blind spot). */
  diskBuild: string;
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
  swBuild: '',
  diskBuild: '',
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
        shipTelemetry('session-ready');
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
          // Zoom/Teams: remote audio is captured PER-PARTICIPANT in-page now —
          // the document_start WebRTC hook mirrors each remote track into a
          // hidden <audio> that inpage's per-element capture picks up (multi-
          // channel). No mixed tabCapture. (startTabAudio remains available as a
          // manual fallback via the toolbar path if ever needed.)
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
  shipTelemetry('session-stop');
  trackFrames.clear();
  lastSpeakerMap = {};
  for (const k of Object.keys(pageDiags)) delete pageDiags[k];
}

// Pack and forward one per-speaker PCM chunk.
const trackFrames = new Map<number, { frames: number; lastAt: number }>();
function sendAudio(index: number, pcm: number[]): void {
  const t = trackFrames.get(index) || { frames: 0, lastAt: 0 };
  t.frames++; t.lastAt = Date.now();
  trackFrames.set(index, t);
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const samples = Float32Array.from(pcm);
  const buf = new ArrayBuffer(4 + samples.byteLength);
  new DataView(buf).setInt32(0, index, true);
  new Float32Array(buf, 4).set(samples);
  ws.send(buf);
}

// ── Telemetry: merged extension state → ingest server /telemetry ──────────
// Page diag (hook/attribution/captured-element state) + session state + per-
// track frame counters, POSTed every 10s while a session is live (plus on
// status changes). Server keeps a ring buffer readable via GET /telemetry —
// debugging needs no client-side copy-paste. Transcript text never enters
// telemetry (page diag scrubs DOM free-text to random words on exit).
const pageDiags: Record<string, any> = {};
let lastSpeakerMap: Record<string, string> = {};
async function shipTelemetry(reason: string): Promise<void> {
  try {
    const cfg = await chrome.storage.local.get(['ingestUrl']);
    const ingest: string = cfg.ingestUrl || 'ws://localhost:8092/ingest';
    const url = ingest.replace(/^ws/, 'http').replace(/\/ingest.*$/, '/telemetry');
    const frames: Record<string, any> = {};
    for (const [idx, t] of trackFrames) frames[idx] = { frames: t.frames, msSinceAudio: Date.now() - t.lastAt };
    await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        reason,
        state: { ...state },
        speakerMap: lastSpeakerMap,
        trackFrames: frames,
        wsOpen: !!ws && ws.readyState === WebSocket.OPEN,
        page: Object.values(pageDiags).find((d: any) => d?.top) || null,
        frames: pageDiags,
      }),
    });
  } catch { /* telemetry must never break capture */ }
}
setInterval(() => {
  if (state.status === 'capturing' || state.status === 'connecting' || state.status === 'error') shipTelemetry('interval');
}, 10000);

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
    case 'RELOAD_NOW':
      // Dev escape hatch: force the deferred reload even mid-capture so a stale
      // background can be replaced on demand.
      stopCapture().finally(() => chrome.runtime.reload());
      sendResponse({ ok: true }); break;
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
      Object.assign(lastSpeakerMap, msg.speakers || {});
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'speakers', speakers: msg.speakers }));
      break;
    case 'diag':
      if (msg.diag) pageDiags[`${msg.diag.top ? 'top' : 'sub'}:${msg.diag.frame || '?'}`] = msg.diag;
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

// If the captured tab RELOADS mid-session (user refresh, SPA hard nav), the
// fresh page never saw BEGIN_CAPTURE — rewire it automatically so capture and
// attribution resume without manual Stop/Start.
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (tabId !== state.tabId || changeInfo.status !== 'complete') return;
  if (state.status !== 'capturing' && state.status !== 'connecting') return;
  if (state.platform === 'note') return;
  setTimeout(() => {
    chrome.tabs.sendMessage(tabId, { type: 'BEGIN_CAPTURE' }).catch(() => { /* content not ready yet */ });
    shipTelemetry('tab-reloaded-rewire');
  }, 1500);
});

// Toolbar click handling. We do NOT use openPanelOnActionClick, because the
// click on the toolbar icon is the ONLY event that grants activeTab on the
// meeting tab — and chrome.tabCapture.getMediaStreamId needs that activeTab.
// So on every toolbar click we: (1) mint the tab-capture stream id for the
// active tab under this invocation, (2) attach tab audio to a running
// Zoom/Teams session (or stash it for the next Start), and (3) open the panel.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(() => { /* older Chrome */ });
// NOTE: listener is intentionally NOT async, and sidePanel.open() is the FIRST
// statement — it must run synchronously within the click gesture or Chrome
// rejects it ("must be called in response to a user gesture"). Minting the
// tab-capture stream id can happen after: it needs the activeTab grant (which
// the invocation gives and which persists), not the live gesture.
chrome.action.onClicked.addListener((tab) => {
  if (tab?.id == null) return;
  chrome.sidePanel.open({ tabId: tab.id }).catch(() => { /* older Chrome / already open */ });

  const host = tab.url ? (() => { try { return new URL(tab.url!).hostname; } catch { return ''; } })() : '';
  const needsTab = host.endsWith('zoom.us') || host.endsWith('teams.live.com')
    || host.endsWith('teams.microsoft.com') || host === 'teams.cloud.microsoft';
  if (needsTab) {
    chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id }, (id) => {
      if (chrome.runtime.lastError || !id) {
        state.tabAudio = `error:${chrome.runtime.lastError?.message || 'no stream id'}`; broadcastStatus(); return;
      }
      tabStreamId = id;
      if (state.status === 'capturing' && (state.platform === 'zoom' || state.platform === 'teams')) {
        startTabAudio();
      }
    });
  }
});

// Dev auto-reload: build.mjs rewrites build-stamp.txt on every build; when the
// on-disk stamp changes (e.g. dist/ is an SSHFS/rsync mirror of a remote build),
// reload the extension from disk. Cheap local-resource fetch; inert for any
// packaged build where the stamp never changes.
const stampUrl = chrome.runtime.getURL('build-stamp.txt');
let knownStamp: string | null = null;
fetch(stampUrl).then(r => r.text()).then(s => { knownStamp = s; state.swBuild = s; }).catch(() => { /* no stamp */ });
setInterval(async () => {
  try {
    const cur = await fetch(stampUrl, { cache: 'no-store' }).then(r => r.text());
    if (knownStamp && cur && cur !== knownStamp) {
      // Never hot-reload mid-capture — reload() restarts capture, churns the
      // ingest session, and resets the transcription buffer. Defer until idle.
      // The panel surfaces a "background stale → Reload now" banner so the dev
      // isn't stuck on old code (esp. with auto-start keeping capture on).
      if (state.status === 'capturing' || state.status === 'connecting') {
        state.diskBuild = cur; broadcastStatus(); return;
      }
      chrome.runtime.reload();
    }
  } catch { /* stamp unreadable; skip */ }
}, 2000);

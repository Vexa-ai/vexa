/**
 * Background service worker — GOOGLE MEET ONLY.
 *
 * Owns the single WebSocket to the desktop ingest (ws://localhost:9099/ingest).
 * Because it runs in the extension's own context with host permissions, its
 * WebSocket is NOT subject to Google Meet's page CSP. It receives per-speaker
 * PCM chunks from the content script, encodes each into a capture.v1 binary
 * frame (@vexa/capture-codec encodeAudioFrame) and streams it to the desktop.
 *
 * Control flow: panel → START/STOP here → open/close WS and tell the meeting
 * tab's content script to begin/end capture.
 */

import { detectMeeting, MeetingRef } from './meeting';
import { encodeAudioFrame, encodeEvent } from '@vexa/capture-codec';

interface SessionState {
  status: 'idle' | 'connecting' | 'capturing' | 'error';
  /** Capture suspended (audio/hints dropped) but the session stays alive —
   *  resume continues the SAME meeting. Stop ends it. */
  paused: boolean;
  tabId: number | null;
  meetingId: number | null;
  platform: string | null;
  nativeMeetingId: string | null;
  streams: number;
  error: string | null;
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
  paused: false,
  tabId: null,
  meetingId: null,
  platform: null,
  nativeMeetingId: null,
  streams: 0,
  error: null,
  swBuild: '',
  diskBuild: '',
};

// The desktop ingest WS (the all-Node pipeline). Default ws://localhost:9099/ingest.
const DEFAULT_INGEST = 'ws://localhost:9099/ingest';
const DEFAULT_GATEWAY = 'http://localhost:8056';

let ws: WebSocket | null = null;

/** tabCapture stream id, minted on the toolbar-click gesture (that click is the
 *  only event that grants activeTab, which getMediaStreamId requires). Used for
 *  media tabs (YouTube now; zoom/teams slot in later) where the offscreen mixed
 *  captor is the audio source. */
let tabStreamId: string | null = null;

function broadcastStatus(): void {
  chrome.runtime.sendMessage({ type: 'STATUS', state }).catch(() => { /* panel may be closed */ });
}

/** Ensure the offscreen document exists. USER_MEDIA covers the voice-notepad mic
 *  AND the tabCapture getUserMedia; AUDIO_PLAYBACK covers re-playing the captured
 *  tab audio to the speakers (tab capture otherwise mutes the tab). */
async function ensureOffscreen(): Promise<void> {
  const has = await (chrome.offscreen as any).hasDocument?.().catch(() => false);
  if (has) return;
  await chrome.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: [chrome.offscreen.Reason.USER_MEDIA, chrome.offscreen.Reason.AUDIO_PLAYBACK],
    justification: 'Microphone and meeting/media audio capture and re-play',
  }).catch((e) => { if (!String(e).includes('single offscreen')) throw e; });
}

/** Start the mixed tab-audio capture (offscreen) for the current media session. */
function startTabAudio(): void {
  if (!tabStreamId) {
    state.error = 'tab audio needs a click — open the panel from the toolbar icon on this tab';
    broadcastStatus();
    return;
  }
  ensureOffscreen()
    .then(() => chrome.runtime.sendMessage({ type: 'TAB_CAPTURE_START', streamId: tabStreamId }))
    .then((res: any) => {
      if (res && res.ok === false) { state.error = `tab capture: ${res.error}`; broadcastStatus(); }
    })
    .catch((e) => { state.error = `tab capture: ${e.message}`; broadcastStatus(); });
}

async function startCaptureForTab(tabId: number, url: string, meetingRef?: MeetingRef): Promise<void> {
  if (state.status === 'capturing' || state.status === 'connecting') return;

  // An explicit ref wins. No meeting anywhere → voice-notepad mode: capture the
  // mic via the offscreen document under the synthetic 'note' platform.
  const meeting = meetingRef || detectMeeting(url)
    || { platform: 'note' as any, nativeMeetingId: `note-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}` };

  const cfg = await chrome.storage.local.get(['apiKey', 'ingestUrl', 'language']);
  const apiKey: string = cfg.apiKey || '';
  const ingestUrl: string = cfg.ingestUrl || DEFAULT_INGEST;
  const language: string = cfg.language || 'auto';

  state.status = 'connecting';
  state.paused = false;
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
        } else if (state.platform === 'youtube') {
          // Media tab (YouTube): no per-participant <audio>, and the in-page
          // mixed captor's smooth AudioWorklet is blocked by YouTube's CSP →
          // its ScriptProcessor stutters under YouTube's heavy main thread.
          // Capture the tab audio in the OFFSCREEN document instead (a dedicated
          // low-load page where the ScriptProcessor runs smoothly) → channel 999,
          // diarized by the desktop's mixed pipeline. The toolbar click that
          // opened this session already minted the stream id; use it now.
          startTabAudio();
        } else {
          // Page-side capture (Google Meet): local mic ("You") + per-participant
          // <audio> elements, captured in-page by inpage.ts.
          if (state.tabId !== null) {
            chrome.tabs.sendMessage(state.tabId, { type: 'BEGIN_CAPTURE' }).catch(() => { /* content not ready */ });
          }
        }
      } else if (msg.type === 'superseded') {
        // A newer session took over this meeting (SW reload / reconnect race).
        // Stop quietly — the successor is already capturing.
        stopCapture();
      } else if (msg.type === 'ended') {
        // Meeting was stopped server-side (dashboard Stop / API delete) — end
        // capture so the panel and the dashboard agree.
        stopCapture();
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

/** Manual start from the panel — targets the active tab. */
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
  // Stop is a CONTRACT: finalize the meeting NOW (active → completed) via the
  // gateway REST call. The WS close alone leaves the meeting 'active' for the
  // ingest server's reconnect grace (~60s) — "stop" must mean stopped.
  const ended = { platform: state.platform, nativeMeetingId: state.nativeMeetingId };
  if (ended.platform && ended.nativeMeetingId && ended.platform !== 'note') {
    chrome.storage.local.get(['apiKey', 'gatewayUrl']).then((cfg) => {
      const gw = String(cfg.gatewayUrl || DEFAULT_GATEWAY).replace(/\/+$/, '');
      return fetch(`${gw}/extension/sessions/end`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': cfg.apiKey || '' },
        body: JSON.stringify({ platform: ended.platform, native_meeting_id: ended.nativeMeetingId }),
      });
    }).catch(() => { /* non-fatal: the ingest grace-period finalize covers it */ });
  }
  if (state.platform === 'note') {
    chrome.runtime.sendMessage({ type: 'NOTE_CAPTURE_STOP' }).catch(() => { /* offscreen gone */ });
  } else if (state.platform === 'youtube') {
    // Media tab: capture lives in the offscreen, not the content script.
    chrome.runtime.sendMessage({ type: 'TAB_CAPTURE_STOP' }).catch(() => { /* offscreen gone */ });
  } else {
    if (state.tabId !== null) {
      chrome.tabs.sendMessage(state.tabId, { type: 'END_CAPTURE' }).catch(() => { /* tab gone */ });
    }
  }
  tabStreamId = null;
  if (ws) { try { ws.close(); } catch { /* ignore */ } ws = null; }
  state.status = 'idle';
  state.paused = false;
  state.meetingId = null;
  state.streams = 0;
  broadcastStatus();
  shipTelemetry('session-stop');
  trackFrames.clear();
  lastSpeakerMap = {};
  for (const k of Object.keys(pageDiags)) delete pageDiags[k];
}

// Pack and forward one per-speaker PCM chunk.
const trackFrames = new Map<number, { frames: number; lastAt: number }>();
function sendAudio(index: number, pcm: number[], speakerName?: string): void {
  if (state.paused) return; // paused: capture runs, nothing ships
  const t = trackFrames.get(index) || { frames: 0, lastAt: 0 };
  t.frames++; t.lastAt = Date.now();
  trackFrames.set(index, t);
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  // capture.v1 wire codec — capture-time stamped HERE (pre-network), never at receipt.
  // speakerName (gmeet glow, bound at the source) rides on the frame when present.
  ws.send(encodeAudioFrame(index, Date.now(), Float32Array.from(pcm), speakerName));
}

// ── Telemetry: merged extension state → ingest server /telemetry ──────────
// Page diag (attribution/captured-element state) + session state + per-track
// frame counters, POSTed every 10s while a session is live (plus on status
// changes). Server keeps a ring buffer readable via GET /telemetry — debugging
// needs no client-side copy-paste. Transcript text never enters telemetry (page
// diag scrubs DOM free-text to random words on exit).
const pageDiags: Record<string, any> = {};
let lastSpeakerMap: Record<string, string> = {};
async function shipTelemetry(reason: string): Promise<void> {
  try {
    const cfg = await chrome.storage.local.get(['ingestUrl']);
    const ingest: string = cfg.ingestUrl || DEFAULT_INGEST;
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
      startCaptureActiveTab(); sendResponse({ ok: true }); break;
    case 'AUTO_START':
      // Prefer the URL the content script saw at detection time.
      maybeAutoStart(sender.tab?.id, msg.url || sender.tab?.url, msg.meeting); sendResponse({ ok: true }); break;
    case 'STOP':
      stopCapture(); sendResponse({ ok: true }); break;
    case 'PAUSE':
      state.paused = true; broadcastStatus(); shipTelemetry('pause');
      sendResponse({ ok: true }); break;
    case 'RESUME':
      state.paused = false; broadcastStatus(); shipTelemetry('resume');
      sendResponse({ ok: true }); break;
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
      sendAudio(msg.index, msg.pcm, msg.speakerName); break;
    case 'speakers':
      Object.assign(lastSpeakerMap, msg.speakers || {});
      if (ws && ws.readyState === WebSocket.OPEN) {
        const ts = Date.now(); // capture.v1: index→name map → speaker-joined events
        for (const [idx, name] of Object.entries(msg.speakers || {}))
          if (name) ws.send(encodeEvent({ kind: 'speaker-joined', ts, speaker: String(name), detail: { index: Number(idx) } }));
      }
      break;
    case 'diag':
      if (msg.diag) pageDiags[`${msg.diag.top ? 'top' : 'sub'}:${msg.diag.frame || '?'}`] = msg.diag;
      break;
    case 'dom_probe':
      // RESEARCH: route the audio↔tile co-location probe to the desktop for logging.
      if (msg.probe && ws && ws.readyState === WebSocket.OPEN) ws.send(encodeEvent({ kind: 'dom-probe', ts: Date.now(), speaker: '', detail: { probe: msg.probe } } as any));
      break;
    case 'speaker_activity':
      // Timestamped DOM hint for the server-side cluster↔name binder.
      // Dropped while paused — hints must not describe un-shipped audio.
      if (state.paused) break;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(encodeEvent({ kind: 'active-speaker', ts: Date.now(), speaker: msg.name || '', detail: { hint: msg.kind || 'dom-active', isEnd: !!msg.isEnd } }));
      }
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
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (tabId !== state.tabId || changeInfo.status !== 'complete') return;
  if (state.status !== 'capturing' && state.status !== 'connecting') return;
  if (state.platform === 'note') return;
  // Same tab, DIFFERENT meeting (user joined a new call) → the session must
  // restart bound to the new native id, not keep writing into the old meeting.
  const detected = tab?.url ? detectMeeting(tab.url) : null;
  if (detected && state.nativeMeetingId && detected.nativeMeetingId !== state.nativeMeetingId) {
    shipTelemetry('meeting-changed-restart');
    stopCapture().then(() => maybeAutoStart(tabId, tab!.url!));
    return;
  }
  setTimeout(() => {
    chrome.tabs.sendMessage(tabId, { type: 'BEGIN_CAPTURE' }).catch(() => { /* content not ready yet */ });
    shipTelemetry('tab-reloaded-rewire');
  }, 1500);
});

// On service-worker startup (extension load OR reload), re-inject the capture
// scripts into Meet tabs that are ALREADY open: an extension reload orphans
// every existing tab's content scripts (they can't reach the new SW), which
// repeatedly cost us live sessions silently running stale code. inpage.ts does
// a graceful takeover of any orphaned instance.
async function reinjectIntoOpenMeetingTabs(): Promise<void> {
  let tabs: chrome.tabs.Tab[] = [];
  try { tabs = await chrome.tabs.query({ url: ['https://meet.google.com/*'] }); } catch { return; }
  for (const t of tabs) {
    if (t.id == null) continue;
    try {
      await chrome.scripting.executeScript({ target: { tabId: t.id, allFrames: true }, files: ['content.js'] });
      await chrome.scripting.executeScript({ target: { tabId: t.id, allFrames: true }, files: ['inpage.js'], world: 'MAIN' });
    } catch { /* chrome:// or unloadable frame — skip */ }
  }
}
reinjectIntoOpenMeetingTabs();

// Toolbar click handling. We do NOT use openPanelOnActionClick, because the click
// on the toolbar icon is the ONLY event that grants activeTab on the current tab
// — and chrome.tabCapture.getMediaStreamId needs that activeTab. So on a media
// tab (YouTube now; zoom/teams slot into the predicate later) we also mint the
// tab-capture stream id under this invocation and stash it for the next Start.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(() => { /* older Chrome */ });
// NOTE: listener is intentionally NOT async, and sidePanel.open() is the FIRST
// statement — it must run synchronously within the click gesture or Chrome
// rejects it ("must be called in response to a user gesture"). Minting the
// tab-capture stream id can happen after: it needs the activeTab grant (which the
// invocation gives and which persists), not the live gesture.
chrome.action.onClicked.addListener((tab) => {
  if (tab?.id == null) return;
  chrome.sidePanel.open({ tabId: tab.id }).catch(() => { /* older Chrome / already open */ });

  // Media tabs (YouTube) capture the whole tab's audio via the offscreen mixed
  // captor — mint the stream id now while activeTab is granted.
  const needsTab = !!(tab.url && detectMeeting(tab.url)?.platform === 'youtube');
  if (needsTab) {
    chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id }, (id) => {
      if (chrome.runtime.lastError || !id) {
        state.error = `tab audio: ${chrome.runtime.lastError?.message || 'no stream id'}`; broadcastStatus(); return;
      }
      tabStreamId = id;
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

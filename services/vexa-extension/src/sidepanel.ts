/**
 * Side panel UI — the dashboard's live-transcript experience inside Chrome.
 *
 * Mirrors the dashboard exactly:
 *  - REST bootstrap: GET {gateway}/transcripts/{platform}/{native_id} (X-API-Key)
 *  - Live: WS {gateway}/ws?api_key=… → {action:'subscribe', meetings:[…]} →
 *    'transcript' bundles (per-speaker confirmed[] + pending[]), ping keepalive
 *  - Two-map merge model: confirmed by segment_id (append-only) + pending
 *    replaced per speaker per tick (same as use-live-transcripts.ts)
 *  - Rendering: colored speaker names, grouped consecutive turns, drafts in
 *    muted italic (same as transcript-segment.tsx / SPEAKER_COLORS)
 *
 * Capture control stays in the background worker (START/STOP/STATUS messages).
 */

interface Segment {
  segment_id?: string;
  speaker: string;
  text: string;
  start: number;
  completed: boolean;
  absolute_start_time?: string;
  language?: string;
}

interface PanelState {
  status: 'idle' | 'connecting' | 'capturing' | 'error';
  platform: string | null;
  nativeMeetingId: string | null;
  meetingId: number | null;
  streams: number;
  error: string | null;
}

const $ = (id: string) => document.getElementById(id)!;

// Dashboard SPEAKER_COLORS, mid-stops for light+dark legibility
const SPEAKER_COLORS = ['#2563eb', '#059669', '#9333ea', '#d97706', '#e11d48', '#0891b2', '#4f46e5', '#0d9488'];
const PLATFORMS: Record<string, { name: string; color: string }> = {
  google_meet: { name: 'Google Meet', color: '#22c55e' },
  zoom: { name: 'Zoom', color: '#3b82f6' },
  teams: { name: 'Teams', color: '#8b5cf6' },
  note: { name: 'Voice note', color: '#f59e0b' },
};

const FIELDS = ['apiKey', 'ingestUrl', 'gatewayUrl', 'dashboardUrl', 'language'] as const;
const DEFAULTS: Record<string, string> = {
  ingestUrl: 'ws://localhost:8092/ingest',
  gatewayUrl: 'http://localhost:8056',
  dashboardUrl: 'http://localhost:3001',
  language: 'auto',
};

let cfg: Record<string, string> = { ...DEFAULTS };
let state: PanelState = { status: 'idle', platform: null, nativeMeetingId: null, meetingId: null, streams: 0, error: null };

// Transcript state — the dashboard's two-map model
let confirmed: Map<string, Segment> = new Map();
let pendingBySpeaker: Map<string, Segment[]> = new Map();
let speakerOrder: string[] = [];
let liveWs: WebSocket | null = null;
let livePing: ReturnType<typeof setInterval> | null = null;
let liveFor: string | null = null;
let captureStartMs: number | null = null;

// ── Config ──────────────────────────────────────────────────────

async function loadConfig(): Promise<void> {
  const stored = await chrome.storage.local.get([...FIELDS, 'autoStart']);
  for (const f of FIELDS) {
    cfg[f] = stored[f] || DEFAULTS[f] || '';
    ($(f) as HTMLInputElement).value = cfg[f];
  }
  ($('autoStart') as HTMLInputElement).checked = stored.autoStart !== false;
}

function bindSettings(): void {
  for (const f of FIELDS) {
    $(f).addEventListener('change', () => {
      cfg[f] = ($(f) as HTMLInputElement).value.trim();
      chrome.storage.local.set({ [f]: cfg[f] });
      if (f === 'apiKey' || f === 'gatewayUrl') verifyConnection();
    });
  }
  $('autoStart').addEventListener('change', () =>
    chrome.storage.local.set({ autoStart: ($('autoStart') as HTMLInputElement).checked }));
}

async function verifyConnection(): Promise<void> {
  const row = $('connRow'), text = $('connText');
  row.classList.remove('ok');
  if (!cfg.apiKey) { text.textContent = 'No API key set'; return; }
  text.textContent = 'Checking…';
  try {
    const resp = await fetch(`${cfg.gatewayUrl}/bots`, { headers: { 'X-API-Key': cfg.apiKey } });
    if (resp.ok) { row.classList.add('ok'); text.textContent = 'Connected'; }
    else text.textContent = `Gateway responded ${resp.status}`;
  } catch {
    text.textContent = 'Gateway unreachable';
  }
}

// ── Background sync (capture state) ─────────────────────────────

function applyState(s: PanelState): void {
  const prev = state;
  state = s;
  const pill = $('statusPill'), pillText = $('statusText');
  pill.classList.toggle('live', s.status === 'capturing');
  pillText.textContent =
    s.status === 'capturing' ? 'LIVE'
    : s.status === 'connecting' ? 'Connecting'
    : s.status === 'error' ? 'Error'
    : 'Idle';
  const toggle = $('toggleBtn') as HTMLButtonElement;
  if (s.status === 'capturing' || s.status === 'connecting') {
    toggle.innerHTML = '&#10074;&#10074; Pause';
    toggle.classList.remove('danger');
  } else {
    toggle.innerHTML = '&#9654; Start';
    toggle.classList.remove('danger');
  }

  const bar = $('meetingBar');
  if (s.platform && s.nativeMeetingId && s.status !== 'idle') {
    bar.style.display = 'flex';
    const p = PLATFORMS[s.platform] || { name: s.platform, color: '#8e8e8e' };
    ($('platDot') as HTMLElement).style.background = p.color;
    $('platName').textContent = p.name;
    $('meetingCode').textContent = s.nativeMeetingId;
  } else if (s.status === 'idle') {
    bar.style.display = 'none';
  }

  if (s.status === 'capturing' && s.nativeMeetingId) {
    if (!captureStartMs || prev.status !== 'capturing') captureStartMs = Date.now();
    startLive(s.platform!, s.nativeMeetingId);
  } else if (s.status === 'idle' && liveWs) {
    stopLive();
  }
  if (s.status === 'error' && s.error === 'mic-permission') {
    $('feed').innerHTML = `<div class="empty"><div style="font-size:22px;">&#127908;</div>`
      + `<div>Microphone access is needed for voice notes.</div>`
      + `<button class="btn" id="micGrantBtn" style="max-width:200px;margin-top:6px;">Allow microphone</button></div>`;
    const b = document.getElementById('micGrantBtn');
    if (b) b.addEventListener('click', () => chrome.tabs.create({ url: chrome.runtime.getURL('mic-permission.html') }));
  } else if (s.status === 'error' && s.error) {
    $('feed').innerHTML = `<div class="empty"><div>&#9888;</div><div>${escapeHtml(s.error)}</div></div>`;
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'STATUS' && msg.state) applyState(msg.state as PanelState);
});

async function pollStatus(): Promise<void> {
  const resp = await chrome.runtime.sendMessage({ type: 'STATUS' }).catch(() => null);
  if (resp && resp.state) applyState(resp.state as PanelState);
}

// ── Live transcript feed (same protocol as the dashboard) ───────

function toSegment(s: any): Segment {
  return {
    segment_id: s.segment_id || s.id || s.absolute_start_time,
    speaker: s.speaker || '',
    text: (s.text || '').trim(),
    start: s.start ?? s.start_time ?? 0,
    completed: s.completed !== false,
    absolute_start_time: s.absolute_start_time || s.created_at,
    language: s.language,
  };
}

function feedStatus(text: string, isError = false): void {
  const el = $('feedStatus');
  el.textContent = text;
  el.style.display = text ? 'block' : 'none';
  el.style.color = isError ? 'var(--destructive)' : 'var(--muted-foreground)';
  console.log(`[vexa-panel] ${text}`);
}

async function startLive(platform: string, nativeId: string): Promise<void> {
  const key = `${platform}/${nativeId}`;
  // CONNECTING(0) or OPEN(1) for the same meeting → leave it alone. Tearing
  // down a CONNECTING socket on every 3s status poll caused reconnect churn.
  if (liveFor === key && liveWs && liveWs.readyState <= WebSocket.OPEN) return;
  stopLive();
  liveFor = key;
  confirmed = new Map();
  pendingBySpeaker = new Map();
  speakerOrder = [];

  // 1. REST bootstrap (history)
  feedStatus('Loading transcript…');
  try {
    const resp = await fetch(`${cfg.gatewayUrl}/transcripts/${platform}/${nativeId}`, {
      headers: { 'X-API-Key': cfg.apiKey },
    });
    if (resp.ok) {
      const data = await resp.json();
      for (const raw of data.segments || []) {
        const seg = toSegment(raw);
        if (seg.text && seg.segment_id) confirmed.set(seg.segment_id, seg);
      }
      feedStatus('');
    } else {
      feedStatus(`History fetch failed: HTTP ${resp.status} from ${cfg.gatewayUrl}`, true);
    }
  } catch (err: any) {
    feedStatus(`History fetch failed: ${err.message} (${cfg.gatewayUrl})`, true);
  }
  render();

  // 2. WS live subscribe
  const wsBase = cfg.gatewayUrl.replace(/^http/, 'ws');
  let ws: WebSocket;
  try {
    ws = new WebSocket(`${wsBase}/ws?api_key=${encodeURIComponent(cfg.apiKey)}`);
  } catch (err: any) {
    feedStatus(`Live socket failed to open: ${err.message}`, true);
    return;
  }
  liveWs = ws;
  ws.onopen = () => {
    ws.send(JSON.stringify({ action: 'subscribe', meetings: [{ platform, native_id: nativeId }] }));
    livePing = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: 'ping' }));
    }, 25000);
  };
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'subscribed') { feedStatus(''); return; }
      if (msg.type === 'error') { feedStatus(`Live feed error: ${msg.error}${msg.details ? ` — ${JSON.stringify(msg.details)}` : ''}`, true); return; }
      if (msg.type !== 'transcript') return;
      const speaker = msg.speaker || '';
      for (const raw of msg.confirmed || []) {
        const seg = toSegment(raw);
        if (seg.text && seg.segment_id) confirmed.set(seg.segment_id, seg);
      }
      const pend = (msg.pending || []).map(toSegment).filter((s: Segment) => s.text);
      pendingBySpeaker.set(speaker, pend);
      render();
    } catch { /* ignore malformed frame */ }
  };
  ws.onerror = () => feedStatus(`Live socket error (${wsBase}/ws)`, true);
  ws.onclose = (ev) => {
    if (livePing) { clearInterval(livePing); livePing = null; }
    if (liveFor === key && state.status === 'capturing') {
      feedStatus(`Live socket closed (code ${ev.code}) — reconnecting…`, ev.code !== 1000);
      setTimeout(() => { if (liveFor === key && state.status === 'capturing') startLive(platform, nativeId); }, 2000);
    }
  };
}

function stopLive(): void {
  if (livePing) { clearInterval(livePing); livePing = null; }
  if (liveWs) { liveFor = null; try { liveWs.close(1000); } catch { /* ignore */ } liveWs = null; }
}

// ── Rendering (dashboard transcript style) ──────────────────────

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
}

function speakerColor(name: string): string {
  let idx = speakerOrder.indexOf(name);
  if (idx === -1) { speakerOrder.push(name); idx = speakerOrder.length - 1; }
  return SPEAKER_COLORS[idx % SPEAKER_COLORS.length];
}

function fmtTime(seg: Segment): string {
  if (seg.absolute_start_time) {
    try {
      const iso = seg.absolute_start_time.endsWith('Z') ? seg.absolute_start_time : seg.absolute_start_time + 'Z';
      const d = new Date(iso);
      return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
    } catch { /* fall through */ }
  }
  const m = Math.floor(seg.start / 60), s = Math.floor(seg.start % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function render(): void {
  const feed = $('feed');
  const all: Segment[] = [...confirmed.values()].sort((a, b) =>
    (a.absolute_start_time || '').localeCompare(b.absolute_start_time || '') || a.start - b.start);
  for (const segs of pendingBySpeaker.values()) {
    for (const s of segs) all.push({ ...s, completed: false });
  }
  if (all.length === 0) {
    feed.innerHTML = `<div class="empty" id="emptyState"><div style="font-size:22px;">&#127911;</div><div>Listening… speak and the transcript appears here.</div></div>`;
    return;
  }

  // Group consecutive same-speaker segments into turns (transcript-viewer behavior)
  let html = '';
  let curSpeaker: string | null = null;
  const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60;
  for (const seg of all) {
    if (seg.speaker !== curSpeaker) {
      if (curSpeaker !== null) html += '</div>';
      curSpeaker = seg.speaker;
      html += `<div class="turn"><p class="head"><span class="speaker" style="color:${speakerColor(seg.speaker)}">${escapeHtml(seg.speaker)}</span><span class="time">${fmtTime(seg)}</span></p>`;
    }
    html += `<p class="seg${seg.completed ? '' : ' pending'}">${escapeHtml(seg.text)}</p>`;
  }
  if (curSpeaker !== null) html += '</div>';
  feed.innerHTML = html;
  if (nearBottom) feed.scrollTop = feed.scrollHeight;
}

// ── UI wiring ───────────────────────────────────────────────────

function showSettings(show: boolean): void {
  $('settings').style.display = show ? 'flex' : 'none';
  $('feed').style.display = show ? 'none' : 'block';
  $('meetingBar').style.display = show ? 'none' : (state.platform && state.status !== 'idle' ? 'flex' : 'none');
  if (show) verifyConnection();
}

$('gearBtn').addEventListener('click', () => showSettings($('settings').style.display !== 'flex'));
$('toggleBtn').addEventListener('click', () => {
  if (state.status === 'capturing' || state.status === 'connecting') {
    chrome.runtime.sendMessage({ type: 'STOP' });
  } else {
    // START always fires immediately. Note mode acquires the mic in the
    // offscreen document; if permission is missing it fails fast and we show
    // the "Allow microphone" action below — no flaky side-panel getUserMedia.
    chrome.runtime.sendMessage({ type: 'START' });
    showSettings(false);
  }
});
$('dashBtn').addEventListener('click', () => {
  const url = state.meetingId ? `${cfg.dashboardUrl}/meetings/${state.meetingId}` : cfg.dashboardUrl;
  chrome.tabs.create({ url });
});

// Elapsed clock
setInterval(() => {
  if (state.status === 'capturing' && captureStartMs) {
    const s = Math.floor((Date.now() - captureStartMs) / 1000);
    $('elapsed').textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }
}, 1000);

// Theme-aware logo (dashboard logo.tsx inverse convention)
function applyLogo(): void {
  const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  ($('logo') as HTMLImageElement).src = dark ? 'assets/vexalight.svg' : 'assets/vexadark.svg';
}
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyLogo);

// Dev auto-reload (same stamp the background watches): the MV3 service worker
// can idle out, but this page lives while the panel is open — between them the
// reload always lands.
const stampUrl = chrome.runtime.getURL('build-stamp.txt');
let knownStamp: string | null = null;
fetch(stampUrl).then(r => r.text()).then(s => { knownStamp = s; }).catch(() => { /* no stamp */ });
setInterval(async () => {
  try {
    const cur = await fetch(stampUrl, { cache: 'no-store' }).then(r => r.text());
    if (knownStamp && cur && cur !== knownStamp) {
      if (state.status === 'capturing' || state.status === 'connecting') return; // never reload mid-capture
      chrome.runtime.reload();
    }
  } catch { /* stamp unreadable; skip */ }
}, 2000);

(async () => {
  applyLogo();
  // Visible build identity — confirms which build is loaded after hot reload
  try {
    const raw = await fetch(stampUrl).then(r => r.text());
    const stamp = JSON.parse(raw);
    $('buildTag').textContent = `build ${stamp.human}`;
    $('buildHeader').textContent = stamp.human;
  } catch { $('buildTag').textContent = 'build unknown'; }
  await loadConfig();
  bindSettings();
  await pollStatus();
  if (!cfg.apiKey) showSettings(true);
  setInterval(pollStatus, 3000);
})();

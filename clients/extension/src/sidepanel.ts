/**
 * Side panel UI — capture control for the in-tab Google Meet driver.
 *
 * This panel drives the capture session (Start / Pause / Stop / settings /
 * status) through the background worker (START/STOP/STATUS messages). The live
 * transcript itself is read on the Vexa dashboard (the "Dashboard ↗" button) —
 * the panel intentionally does NOT bundle the transcript-rendering brick.
 *
 * Capture flows: panel Start → background opens the ingest WS
 * (ws://localhost:9099/ingest, the desktop) → tells the Meet tab's content
 * script to begin per-participant capture.
 */

interface PanelState {
  status: 'idle' | 'connecting' | 'capturing' | 'error';
  paused?: boolean;
  platform: string | null;
  nativeMeetingId: string | null;
  meetingId: number | null;
  streams: number;
  error: string | null;
  swBuild?: string;
  diskBuild?: string;
}

// build-stamp.txt as this panel doc loaded — compared to the SW's swBuild to
// detect a stale background (reload deferred during capture).
let panelBuild = '';
fetch(chrome.runtime.getURL('build-stamp.txt')).then(r => r.text()).then(s => { panelBuild = s; }).catch(() => { /* none */ });

const $ = (id: string) => document.getElementById(id)!;

const PLATFORMS: Record<string, { name: string; color: string }> = {
  google_meet: { name: 'Google Meet', color: '#22c55e' },
  note: { name: 'Voice note', color: '#f59e0b' },
};

const FIELDS = ['apiKey', 'ingestUrl', 'gatewayUrl', 'dashboardUrl', 'language'] as const;
const DEFAULTS: Record<string, string> = {
  ingestUrl: 'ws://localhost:9099/ingest',
  gatewayUrl: 'http://localhost:8056',
  dashboardUrl: 'http://localhost:3001',
  language: 'auto',
};

let cfg: Record<string, string> = { ...DEFAULTS };
let state: PanelState = { status: 'idle', platform: null, nativeMeetingId: null, meetingId: null, streams: 0, error: null };

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

function feedStatus(text: string, isError = false): void {
  const el = $('feedStatus');
  el.textContent = text;
  el.style.display = text ? 'block' : 'none';
  el.style.color = isError ? 'var(--destructive)' : 'var(--muted-foreground)';
  console.log(`[vexa-panel] ${text}`);
}

function renderFeed(): void {
  const feed = $('feed');
  if (state.status === 'capturing' && !state.paused) {
    feed.innerHTML = `<div class="empty"><div style="font-size:22px;">&#127911;</div>`
      + `<div>Capturing ${state.streams} stream(s). Open the dashboard to read the live transcript.</div>`
      + `<button class="btn" id="feedDashBtn" style="max-width:220px;margin-top:8px;">Open dashboard &#8599;</button></div>`;
    const b = document.getElementById('feedDashBtn');
    if (b) b.addEventListener('click', openDashboard);
  } else if (state.status === 'capturing' && state.paused) {
    feed.innerHTML = `<div class="empty"><div style="font-size:22px;">&#10074;&#10074;</div><div>Paused — press Resume to keep capturing.</div></div>`;
  } else {
    feed.innerHTML = `<div class="empty" id="emptyState"><div style="font-size:22px;">&#127911;</div>`
      + `<div>Join a Google Meet and Vexa will capture it. Read the transcript on the dashboard.</div></div>`;
  }
}

function applyState(s: PanelState): void {
  state = s;

  // Stale-background guard: if the running service worker loaded an older build
  // than this panel, capture-control code is out of date. Surface a blocking
  // banner with a one-click forced reload.
  const newerOnDisk = s.diskBuild && s.swBuild && s.diskBuild !== s.swBuild;
  if (newerOnDisk || (s.swBuild && panelBuild && s.swBuild !== panelBuild)) {
    const el = $('feedStatus');
    el.style.display = 'block';
    el.style.color = 'var(--destructive)';
    el.innerHTML = `⚠ Background is running an old build — fixes won't apply. `
      + `<button class="btn" id="reloadSwBtn" style="display:inline-block;padding:3px 10px;font-size:12px;margin-left:6px;">Reload now</button>`
      + `<div style="margin-top:6px;font-size:11px;opacity:.85;">After it reloads, also refresh the meeting tab (Cmd/Ctrl+R) — an extension reload orphans the page's capture scripts.</div>`;
    const b = document.getElementById('reloadSwBtn');
    if (b && !(b as any).__wired) {
      (b as any).__wired = true;
      b.addEventListener('click', () => { feedStatus('Reloading…'); chrome.runtime.sendMessage({ type: 'RELOAD_NOW' }); });
    }
    return; // don't let other status lines overwrite this — it's the blocker
  }
  const pill = $('statusPill'), pillText = $('statusText');
  pill.classList.toggle('live', s.status === 'capturing' && !s.paused);
  pillText.textContent =
    s.status === 'capturing' ? (s.paused ? 'Paused' : 'LIVE')
    : s.status === 'connecting' ? 'Connecting'
    : s.status === 'error' ? 'Error'
    : 'Idle';
  const toggle = $('toggleBtn') as HTMLButtonElement;
  const stopBtn = $('stopBtn') as HTMLButtonElement;
  if (s.status === 'capturing' || s.status === 'connecting') {
    // Live: Pause suspends (session stays alive, Resume continues the same
    // meeting); Stop ends it. Two distinct verbs, two buttons.
    toggle.innerHTML = s.paused ? '&#9654; Resume' : '&#10074;&#10074; Pause';
    toggle.classList.remove('danger');
    stopBtn.style.display = '';
  } else {
    toggle.innerHTML = '&#9654; Start';
    toggle.classList.remove('danger');
    stopBtn.style.display = 'none';
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
    if (!captureStartMs) captureStartMs = Date.now();
  } else if (s.status === 'idle') {
    captureStartMs = null;
  }

  if (s.error === 'mic-permission') {
    // Mic blocked at the extension origin (note mode). Surface the grant.
    $('feed').innerHTML = `<div class="empty"><div style="font-size:22px;">&#127908;</div>`
      + `<div>Microphone access is needed.</div>`
      + `<button class="btn" id="micGrantBtn" style="max-width:200px;margin-top:6px;">Allow microphone</button></div>`;
    const b = document.getElementById('micGrantBtn');
    if (b) b.addEventListener('click', () => chrome.tabs.create({ url: chrome.runtime.getURL('mic-permission.html') }));
  } else if (s.status === 'error' && s.error) {
    $('feed').innerHTML = `<div class="empty"><div>&#9888;</div><div>${escapeHtml(s.error)}</div></div>`;
  } else {
    feedStatus('');
    renderFeed();
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'STATUS' && msg.state) applyState(msg.state as PanelState);
});

async function pollStatus(): Promise<void> {
  const resp = await chrome.runtime.sendMessage({ type: 'STATUS' }).catch(() => null);
  if (resp && resp.state) applyState(resp.state as PanelState);
}

// ── Rendering helpers ───────────────────────────────────────────

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
}

function openDashboard(): void {
  const url = state.meetingId ? `${cfg.dashboardUrl}/meetings/${state.meetingId}` : cfg.dashboardUrl;
  chrome.tabs.create({ url });
}

// ── UI wiring ───────────────────────────────────────────────────

function showSettings(show: boolean): void {
  $('settings').style.display = show ? 'flex' : 'none';
  $('feed').style.display = show ? 'none' : 'block';
  $('meetingBar').style.display = show ? 'none' : (state.platform && state.status !== 'idle' ? 'flex' : 'none');
  if (show) verifyConnection();
}

// ── Theme (System / Light / Dark) ───────────────────────────────
type Theme = 'system' | 'light' | 'dark';
const THEME_ORDER: Theme[] = ['system', 'light', 'dark'];
const THEME_ICON: Record<Theme, string> = { system: '☾', light: '☀', dark: '☽' };
const darkMql = window.matchMedia('(prefers-color-scheme: dark)');
let theme: Theme = 'system';

function effectiveDark(): boolean {
  return theme === 'dark' || (theme === 'system' && darkMql.matches);
}

function applyTheme(): void {
  document.documentElement.setAttribute('data-theme', theme);
  // Logo: light-colored logo on dark bg, dark-colored logo on light bg.
  const logo = document.getElementById('logo') as HTMLImageElement | null;
  if (logo) logo.src = effectiveDark() ? 'assets/vexalight.svg' : 'assets/vexadark.svg';
  const btn = $('themeBtn');
  btn.textContent = THEME_ICON[theme];
  btn.title = `Theme: ${theme[0].toUpperCase()}${theme.slice(1)}`;
}

async function initTheme(): Promise<void> {
  const { theme: stored } = await chrome.storage.local.get('theme');
  theme = (stored as Theme) || 'system';
  applyTheme();
  // Follow OS changes only while on System.
  darkMql.addEventListener('change', () => { if (theme === 'system') applyTheme(); });
}

$('themeBtn').addEventListener('click', () => {
  theme = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length];
  chrome.storage.local.set({ theme });
  applyTheme();
});

$('gearBtn').addEventListener('click', () => showSettings($('settings').style.display !== 'flex'));
$('stopBtn').addEventListener('click', () => chrome.runtime.sendMessage({ type: 'STOP' }));
$('toggleBtn').addEventListener('click', async () => {
  if (state.status === 'capturing' || state.status === 'connecting') {
    chrome.runtime.sendMessage({ type: state.paused ? 'RESUME' : 'PAUSE' });
    return;
  }
  chrome.runtime.sendMessage({ type: 'START' });
  showSettings(false);
});
$('dashBtn').addEventListener('click', openDashboard);

// Elapsed clock
setInterval(() => {
  if (state.status === 'capturing' && captureStartMs) {
    const s = Math.floor((Date.now() - captureStartMs) / 1000);
    $('elapsed').textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }
}, 1000);

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
  // Visible build identity — confirms which build is loaded after hot reload
  try {
    const raw = await fetch(stampUrl).then(r => r.text());
    const stamp = JSON.parse(raw);
    $('buildTag').textContent = `build ${stamp.human}`;
    $('buildHeader').textContent = stamp.human;
  } catch { $('buildTag').textContent = 'build unknown'; }
  await initTheme();
  await loadConfig();
  bindSettings();
  await pollStatus();
  if (!cfg.apiKey) showSettings(true);
  setInterval(pollStatus, 3000);
})();

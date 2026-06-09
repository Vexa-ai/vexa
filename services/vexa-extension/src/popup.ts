/** Popup UI — config persistence + start/stop + live status. */

const $ = (id: string) => document.getElementById(id) as HTMLInputElement;
const statusBox = () => document.getElementById('status') as HTMLDivElement;
const statusText = () => document.getElementById('statusText') as HTMLSpanElement;

const FIELDS = ['apiKey', 'ingestUrl', 'language'];

async function loadConfig(): Promise<void> {
  const cfg = await chrome.storage.local.get(FIELDS);
  $('apiKey').value = cfg.apiKey || '';
  $('ingestUrl').value = cfg.ingestUrl || 'ws://localhost:8092/ingest';
  $('language').value = cfg.language || 'auto';
}

function saveField(name: string): void {
  chrome.storage.local.set({ [name]: $(name).value.trim() });
}

function render(state: any): void {
  const box = statusBox();
  box.className = state.status;
  let txt = state.status.charAt(0).toUpperCase() + state.status.slice(1);
  if (state.status === 'capturing') txt += ` — meeting ${state.meetingId ?? '?'}, ${state.streams} stream(s)`;
  if (state.status === 'error' && state.error) txt += ` — ${state.error}`;
  statusText().textContent = txt;
  ($('start') as unknown as HTMLButtonElement).disabled = state.status === 'capturing' || state.status === 'connecting';
  ($('stop') as unknown as HTMLButtonElement).disabled = state.status === 'idle';
}

async function refreshStatus(): Promise<void> {
  const resp = await chrome.runtime.sendMessage({ type: 'STATUS' }).catch(() => null);
  if (resp && resp.state) render(resp.state);
}

document.addEventListener('DOMContentLoaded', () => {
  loadConfig();
  refreshStatus();
  for (const f of FIELDS) $(f).addEventListener('change', () => saveField(f));
  document.getElementById('start')!.addEventListener('click', () => {
    for (const f of FIELDS) saveField(f);
    chrome.runtime.sendMessage({ type: 'START' });
  });
  document.getElementById('stop')!.addEventListener('click', () => {
    chrome.runtime.sendMessage({ type: 'STOP' });
  });
});

// Live status pushes from the background worker.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'STATUS' && msg.state) render(msg.state);
});

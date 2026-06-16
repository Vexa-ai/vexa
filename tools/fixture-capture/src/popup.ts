/**
 * Fixture-capture popup — point at the recorder, Start/Stop, show status.
 * The Start click is the required user gesture for tabCapture (Zoom/Teams mixed
 * audio); we mint the stream id here and hand it to the background, exactly as
 * the product extension's panel does — minus all the transcription/account UI.
 */
const $ = (id: string) => document.getElementById(id) as HTMLElement;
const DEFAULT_INGEST = 'ws://localhost:9099/ingest';

async function load(): Promise<void> {
  const cfg = await chrome.storage.local.get(['ingestUrl']);
  ($('ingestUrl') as HTMLInputElement).value = cfg.ingestUrl || DEFAULT_INGEST;
  refresh();
}

function saveUrl(): Promise<void> {
  return chrome.storage.local.set({ ingestUrl: ($('ingestUrl') as HTMLInputElement).value.trim() || DEFAULT_INGEST });
}

function refresh(): void {
  chrome.runtime.sendMessage({ type: 'STATUS' }, (resp) => {
    const s = resp?.state;
    const capturing = s?.status === 'capturing';
    $('status').textContent = s
      ? `${s.status}${s.error ? ': ' + s.error : ''}${s.platform ? ' — ' + s.platform : ''}${s.meetingId ? ' #' + s.meetingId : ''}${s.streams ? ' · ' + s.streams + ' stream(s)' : ''}`
      : 'idle';
    ($('start') as HTMLButtonElement).disabled = capturing;
    // Stop always available once not idle, so a stuck error/connecting can be reset.
    ($('stop') as HTMLButtonElement).disabled = !s || s.status === 'idle';
  });
}

$('ingestUrl').addEventListener('change', saveUrl);

$('start').addEventListener('click', async () => {
  void saveUrl(); // don't block the click gesture on storage
  // Mint the tabCapture stream id under this click gesture (Zoom/Teams mixed audio).
  let streamId: string | undefined;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const host = tab?.url ? new URL(tab.url).hostname : '';
    const needsTab = host.endsWith('zoom.us') || host.endsWith('teams.live.com')
      || host.endsWith('teams.microsoft.com') || host === 'teams.cloud.microsoft';
    if (needsTab && tab?.id != null) {
      streamId = await new Promise<string>((resolve, reject) =>
        chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id! }, (id) =>
          chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve(id)));
    }
  } catch {
    streamId = undefined; // gmeet (per-element) needs none; don't block Start
  }
  chrome.runtime.sendMessage({ type: 'START', streamId });
  $('status').textContent = 'starting…';
  setTimeout(refresh, 600);
});

$('stop').addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'STOP' });
  setTimeout(refresh, 600);
});

setInterval(refresh, 1500);
load();

/**
 * Dashboard — subscribes to /transcript WebSocket, renders speaker_N chips
 * and the rolling transcript.
 */

const segmentsEl = document.getElementById('segments');
const diarizerNameEl = document.getElementById('diarizer-name');
const numSpeakersEl = document.getElementById('num-speakers');
const trxPillEl = document.getElementById('trx-pill');
const trxUrlEl = document.getElementById('trx-url');

const palette = [
  'var(--speaker-0)',
  'var(--speaker-1)',
  'var(--speaker-2)',
  'var(--speaker-3)',
  'var(--speaker-4)',
  'var(--speaker-5)',
];

let segmentCount = 0;

function fmtTime(ms) {
  const d = new Date(ms);
  return d.toLocaleTimeString();
}

function speakerColor(label) {
  const m = /^speaker_(\d+)$/.exec(label);
  if (!m) return '#666';
  return palette[Number(m[1]) % palette.length];
}

function appendSegment(event) {
  if (segmentCount === 0) {
    segmentsEl.innerHTML = '';
  }
  segmentCount += 1;

  const row = document.createElement('div');
  row.className = 'segment';

  const ts = document.createElement('div');
  ts.className = 'ts';
  ts.textContent = fmtTime(event.t0);

  const chip = document.createElement('div');
  chip.className = 'chip';
  chip.textContent = event.speaker;
  chip.style.background = speakerColor(event.speaker);

  const text = document.createElement('div');
  text.className = 'text' + (event.text.startsWith('[') ? ' placeholder' : '');
  text.textContent = event.text;

  row.append(ts, chip, text);
  segmentsEl.append(row);
  row.scrollIntoView({ block: 'end' });
}

function connect() {
  const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/transcript';
  const ws = new WebSocket(url);

  ws.onmessage = (ev) => {
    let event;
    try { event = JSON.parse(ev.data); } catch { return; }
    switch (event.kind) {
      case 'diarizer-info':
        diarizerNameEl.textContent = event.name;
        numSpeakersEl.textContent = String(event.numSpeakers);
        break;
      case 'transcription-status':
        if (event.reachable) {
          trxPillEl.textContent = 'reachable';
          trxPillEl.className = 'pill ok';
        } else {
          trxPillEl.textContent = 'offline';
          trxPillEl.className = 'pill err';
        }
        trxUrlEl.textContent = event.url || '(unset)';
        break;
      case 'segment':
        appendSegment(event);
        break;
      default:
        break;
    }
  };

  ws.onclose = () => {
    setTimeout(connect, 1000);
  };
  ws.onerror = () => { /* let onclose retry */ };
}

connect();

/**
 * Dashboard — subscribes to /transcript WebSocket, renders the
 * production bot's transcript-bundle shape (confirmed + pending per
 * speaker). Mirrors what a Vexa dashboard subscribing to the
 * `tc:meeting:<id>:mutable` Redis pub/sub channel would render — same
 * event shapes, different transport.
 */

const segmentsEl = document.getElementById('segments');
const diarizerNameEl = document.getElementById('diarizer-name');
const numSpeakersEl = document.getElementById('num-speakers');
const trxPillEl = document.getElementById('trx-pill');
const trxUrlEl = document.getElementById('trx-url');
const sessionUidEl = document.getElementById('session-uid');
const meetingIdEl = document.getElementById('meeting-id');

const palette = [
  'var(--speaker-0)',
  'var(--speaker-1)',
  'var(--speaker-2)',
  'var(--speaker-3)',
  'var(--speaker-4)',
  'var(--speaker-5)',
];

/** Per-speaker DOM elements for the active pending row (single row, updated in place). */
const pendingRows = new Map();
let rendered = 0;

function speakerColor(label) {
  const m = /^speaker_(\d+)$/.exec(label);
  if (!m) return '#666';
  return palette[Number(m[1]) % palette.length];
}

function fmtAbs(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return '';
  }
}

function makeChip(speaker) {
  const chip = document.createElement('div');
  chip.className = 'chip';
  chip.textContent = speaker;
  chip.style.background = speakerColor(speaker);
  return chip;
}

function renderConfirmedSegment(seg) {
  if (rendered === 0) segmentsEl.innerHTML = '';
  rendered += 1;

  const row = document.createElement('div');
  row.className = 'segment';

  const ts = document.createElement('div');
  ts.className = 'ts';
  ts.textContent = fmtAbs(seg.absolute_start_time) || `t=${seg.start?.toFixed?.(2) ?? '?'}s`;

  const text = document.createElement('div');
  text.className = 'text' + (seg.text?.startsWith?.('[') ? ' placeholder' : '');
  text.textContent = seg.text;

  const meta = document.createElement('div');
  meta.className = 'meta';
  const lang = seg.language && seg.language !== 'unknown' ? seg.language : '';
  const dur = (seg.end - seg.start).toFixed(2);
  meta.textContent = `${dur}s${lang ? ' · ' + lang : ''}${seg.segment_id ? ' · ' + seg.segment_id.split(':').slice(-2).join(':') : ''}`;

  row.append(ts, makeChip(seg.speaker), text, meta);
  segmentsEl.append(row);
  row.scrollIntoView({ block: 'end' });
}

function renderPending(speaker, segs) {
  // Replace any prior pending row for this speaker
  const prior = pendingRows.get(speaker);
  if (prior) prior.remove();
  if (!segs || segs.length === 0) {
    pendingRows.delete(speaker);
    return;
  }
  if (rendered === 0) segmentsEl.innerHTML = '';

  const row = document.createElement('div');
  row.className = 'segment pending';

  const ts = document.createElement('div');
  ts.className = 'ts';
  const firstAbs = segs[0]?.absolute_start_time;
  ts.textContent = (firstAbs ? fmtAbs(firstAbs) : '…') + ' (pending)';

  const text = document.createElement('div');
  text.className = 'text';
  text.style.fontStyle = 'italic';
  text.style.opacity = '0.75';
  text.textContent = segs.map((s) => s.text).join(' ');

  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = `${segs.length} pending`;

  row.append(ts, makeChip(speaker), text, meta);
  segmentsEl.append(row);
  pendingRows.set(speaker, row);
  row.scrollIntoView({ block: 'end' });
}

function handleEvent(event) {
  switch (event.kind) {
    case 'session_info':
      diarizerNameEl.textContent = event.diarizer_name;
      numSpeakersEl.textContent = String(event.num_speakers);
      sessionUidEl.textContent = event.session_uid;
      meetingIdEl.textContent = event.meeting_id;
      if (event.transcription_reachable) {
        trxPillEl.textContent = 'reachable';
        trxPillEl.className = 'pill ok';
      } else {
        trxPillEl.textContent = 'offline';
        trxPillEl.className = 'pill err';
      }
      trxUrlEl.textContent = event.transcription_url || '(unset)';
      break;
    case 'session_start':
      // Session boundary marker — visible row
      if (rendered === 0) segmentsEl.innerHTML = '';
      const sb = document.createElement('div');
      sb.className = 'session-boundary';
      sb.textContent = `── session start ${event.uid} @ ${fmtAbs(event.start_timestamp)} ──`;
      segmentsEl.append(sb);
      break;
    case 'session_end':
      const eb = document.createElement('div');
      eb.className = 'session-boundary';
      eb.textContent = `── session end ${event.uid} ──`;
      segmentsEl.append(eb);
      break;
    case 'transcript':
      for (const seg of event.confirmed ?? []) renderConfirmedSegment(seg);
      renderPending(event.speaker, event.pending ?? []);
      break;
    case 'speaker_event':
      // Quiet — could surface in a status pane later
      break;
    default:
      break;
  }
}

function connect() {
  const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/transcript';
  const ws = new WebSocket(url);
  ws.onmessage = (ev) => {
    let event;
    try { event = JSON.parse(ev.data); } catch { return; }
    handleEvent(event);
  };
  ws.onclose = () => setTimeout(connect, 1000);
  ws.onerror = () => { /* let onclose retry */ };
}

connect();

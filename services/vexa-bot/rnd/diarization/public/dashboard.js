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
const metricsGridEl = document.getElementById('metrics-grid');
const metricsElapsedEl = document.getElementById('metrics-elapsed');

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
    case 'metrics':
      renderMetrics(event.snapshot);
      break;
    default:
      break;
  }
}

function card(label, value, sub, cls = '') {
  return `<div class="card ${cls}"><div class="label">${label}</div><div class="v">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ''}</div>`;
}

function fmtMs(ms) {
  if (ms == null || ms === 0) return '—';
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return `${m}m${s.toString().padStart(2, '0')}s`;
}

function renderHistogram(snap) {
  if (!snap || !snap.samples) return '<div class="sub">no samples</div>';
  const total = snap.counts.reduce((a, b) => a + b, 0) || 1;
  const max = Math.max(...snap.counts) || 1;
  const bars = snap.counts.map((c, i) => {
    const h = Math.round((c / max) * 28);
    const isOver = i === snap.counts.length - 1;
    const pct = ((c / total) * 100).toFixed(0);
    return `<div class="bar${isOver ? ' over' : ''}" style="height:${h}px" title="${pct}% (${c})"></div>`;
  }).join('');
  const labels = snap.buckets.map((b) => `<span>${b.toFixed(1)}</span>`).join('') + '<span>∞</span>';
  return `<div class="histo">${bars}</div><div class="histo-row">${labels}</div>`;
}

function renderMetrics(s) {
  if (!s) return;
  metricsElapsedEl.textContent = `· session ${fmtMs(s.session.elapsedMs)} elapsed`;

  // Cluster-allocation churn — color depends on rate.
  const allocRate = s.diarizer.clusterAllocsPerMin;
  const allocClass = allocRate > 6 ? 'bad' : allocRate > 2 ? 'warn' : 'ok';

  const routingDepth = s.routing.pendingFramesDepth;
  const depthClass = routingDepth > 200 ? 'bad' : routingDepth > 100 ? 'warn' : '';

  const tx503 = s.transcription.serviceBusy503;
  const txClass = tx503 > 0 ? 'warn' : '';

  // Diarizer cards
  const diarizerCards = [
    card('Speakers', s.diarizer.clusterCount, `${s.diarizer.clusterAllocations} alloc · ${s.diarizer.clusterMerges} merge`),
    card('Allocs / min', allocRate.toFixed(1), `(60s window)`, allocClass),
    card('Commits', s.diarizer.commits, `${s.diarizer.commitsPerMin.toFixed(1)} / min · mean ${fmtMs(s.diarizer.meanCommitDurMs)}`),
    card('Change-points', s.diarizer.changePoints, `${s.diarizer.changePointsPerMin.toFixed(1)} / min`),
    card('Peek refreshes', s.diarizer.peekRefreshes, `${s.diarizer.peekRefreshesPerMin.toFixed(1)} / min`),
    card('Embed latency', `${fmtMs(s.diarizer.embedLatency.p50)} / ${fmtMs(s.diarizer.embedLatency.p95)}`, `p50 / p95 · n=${s.diarizer.embedLatency.count}`),
    card('Label-emit latency', `${fmtMs(s.labelEmitLatency.p50)} / ${fmtMs(s.labelEmitLatency.p95)}`, `p50 / p95 (utt start → commit)`),
  ];

  const routingCards = [
    card('Pending frames', routingDepth, `peak ${s.routing.pendingFramesMax} · in ${s.routing.framesIn}`, depthClass),
    card('Routed', s.routing.framesRouted, `dropped ${s.routing.framesDropped} · overflow ${s.routing.framesOverflowed}`),
  ];

  const txCards = [
    card('Whisper requests', s.transcription.requests, `${s.transcription.successes} ok · ${s.transcription.fatalErrors} fatal`),
    card('Whisper latency', `${fmtMs(s.transcription.requestLatency.p50)} / ${fmtMs(s.transcription.requestLatency.p95)}`, `p50 / p95 · n=${s.transcription.requestLatency.count}`),
    card('503 busy', tx503, `total since session start`, txClass),
  ];

  // Per-speaker breakdown
  const speakerEntries = Object.entries(s.diarizer.perSpeakerCommittedMs).sort((a, b) => b[1] - a[1]);
  const speakerHtml = speakerEntries.length === 0 ? '' :
    `<div class="row" style="margin-top:8px"><span class="title">Per-speaker committed audio:</span>` +
    speakerEntries.map(([id, ms]) => `<span style="background:${speakerColor(id)}; color:#fff; padding:2px 8px; border-radius:10px; font-weight:600;">${id}</span> <span>${fmtMs(ms)}</span>`).join(' ') +
    `</div>`;

  metricsGridEl.innerHTML = [
    ...diarizerCards,
    ...routingCards,
    ...txCards,
  ].join('');

  // Histograms below the grid — append as separate cards spanning the grid.
  const histoEl = document.createElement('div');
  histoEl.className = 'card';
  histoEl.style.gridColumn = 'span 3';
  histoEl.innerHTML = `<div class="label">Centroid distance histogram (commit-time)</div>${renderHistogram(s.diarizer.centroidDistHistogram)}`;
  metricsGridEl.appendChild(histoEl);

  const histoEl2 = document.createElement('div');
  histoEl2.className = 'card';
  histoEl2.style.gridColumn = 'span 3';
  histoEl2.innerHTML = `<div class="label">Turn distance histogram (utt → utt)</div>${renderHistogram(s.diarizer.turnDistHistogram)}`;
  metricsGridEl.appendChild(histoEl2);

  if (speakerHtml) {
    const breakdown = document.createElement('div');
    breakdown.style.gridColumn = '1 / -1';
    breakdown.innerHTML = speakerHtml;
    metricsGridEl.appendChild(breakdown);
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

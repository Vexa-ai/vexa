import { createTranscriptManager, groupSegments } from '/_shared/transcript-rendering.js';
import { buildContestPlan, toTranscriptSegment } from './teams-csrc-live-model.mjs';
import { safeResourceUrl } from './safe-resource-url.mjs';

const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[character]);
const formatClock = (seconds) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;
const formatLatency = (milliseconds) => Number.isFinite(milliseconds) ? `+${(milliseconds / 1000).toFixed(1)}s` : '—';
const colors = ['#58a6ff', '#3fb950', '#d29922', '#db61a2', '#a371f7', '#f0883e', '#39c5cf', '#ff7b72'];
const colorFor = (name) => {
  let hash = 0;
  for (let index = 0; index < name.length; index++) hash = (hash * 31 + name.charCodeAt(index)) | 0;
  return colors[Math.abs(hash) % colors.length];
};

export function mountTeamsPipelineLive(root, reference, { audioUrl, streamUrl, startUrl }) {
  const cutStartMs = Number(reference.slice.cutStartMs);
  const cutStartSec = Number(reference.slice.startSec);
  const durationSec = Number(reference.slice.durationSec);
  const plans = buildContestPlan(reference);
  const plansBySegment = new Map();
  for (const plan of plans) {
    const left = plansBySegment.get(String(plan.segmentId)) ?? [];
    left.push(plan); plansBySegment.set(String(plan.segmentId), left);
    const right = plansBySegment.get(String(plan.rivalSegmentId)) ?? [];
    right.push(plan); plansBySegment.set(String(plan.rivalSegmentId), right);
  }

  root.innerHTML = `
    <header>
      <div class="topbar">
        <span class="brand">Vexa</span>
        <span class="meeting"><span class="platform-dot"></span>Microsoft Teams · actual candidate pipeline · m26123</span>
        <span class="live-pill" id="live-pill"><span class="dot"></span><span id="live-label">Ready</span></span>
      </div>
      <div class="transport">
        <audio id="audio" preload="auto"></audio>
        <span id="audio-state">Audio ready</span>
        <button type="button" id="start">Join replayed call</button>
      </div>
    </header>
    <section class="metrics" aria-label="Live transcript timing">
      <div class="metric"><div class="metric-label">Call position</div><div class="metric-value" id="position">${formatClock(cutStartSec)} / ${formatClock(cutStartSec + durationSec)}</div></div>
      <div class="metric"><div class="metric-label">Latest first visible</div><div class="metric-value" id="first-visible">—</div></div>
      <div class="metric"><div class="metric-label">Latest transcript update</div><div class="metric-value" id="update-latency">—</div></div>
      <div class="metric"><div class="metric-label">Current state</div><div class="metric-value" id="state-count">0 confirmed · 0 pending</div></div>
    </section>
    <main id="feed"><div class="empty"><strong>Ready to join the replayed call</strong><span>Press Join. Captured inputs will then enter the actual candidate pipeline on wall clock.</span></div></main>`;

  const audio = root.querySelector('#audio');
  const feed = root.querySelector('#feed');
  const livePill = root.querySelector('#live-pill');
  const liveLabel = root.querySelector('#live-label');
  const audioState = root.querySelector('#audio-state');
  const position = root.querySelector('#position');
  const firstVisible = root.querySelector('#first-visible');
  const updateLatency = root.querySelector('#update-latency');
  const stateCount = root.querySelector('#state-count');
  const start = root.querySelector('#start');
  audio.src = safeResourceUrl(audioUrl, { allowBlob: true });

  let manager = createTranscriptManager();
  let received = [];
  let activeContests = new Map();
  let confirmedIds = new Set();
  let firstSeen = new Set();
  let latestFirstVisibleMs = null;
  let latest = null;
  let visible = [];
  let startedAtWallMs = null;
  let raf = null;

  const contestFor = (segmentId) => activeContests.get(String(segmentId)) ?? null;
  const apply = (event) => {
    const segment = toTranscriptSegment(event, cutStartMs, contestFor(event.segmentId));
    manager.handleMessage(segment.completed
      ? { type: 'transcript', speaker: segment.speaker, confirmed: [segment], pending: [] }
      : { type: 'transcript', speaker: segment.speaker, confirmed: [], pending: segment.text.trim() ? [segment] : [] });
    visible = manager.getSegments();
    latest = segment;
    if (segment.text.trim() && !firstSeen.has(segment.segment_id)) {
      firstSeen.add(segment.segment_id);
      latestFirstVisibleMs = segment.latencyMs;
    }
  };
  const rebuild = () => {
    manager = createTranscriptManager();
    firstSeen = new Set();
    latestFirstVisibleMs = null;
    latest = null;
    visible = [];
    for (const event of received) apply(event);
  };
  const activateAvailableContests = (segmentId) => {
    let changed = false;
    for (const plan of plansBySegment.get(String(segmentId)) ?? []) {
      if (!confirmedIds.has(String(plan.segmentId)) || !confirmedIds.has(String(plan.rivalSegmentId))) continue;
      if (activeContests.has(String(plan.segmentId))) continue;
      activeContests.set(String(plan.segmentId), plan);
      activeContests.set(String(plan.rivalSegmentId), plan);
      changed = true;
    }
    return changed;
  };
  const render = () => {
    const groups = groupSegments(visible);
    feed.innerHTML = groups.length ? groups.map((group) => {
      const name = group.key || 'Speaker';
      const rows = group.segments.map((segment) => {
        const state = segment.completed === false ? 'DRAFT' : 'FINAL';
        const csrc = Number.isFinite(Number(segment.csrc)) ? ` · CSRC ${segment.csrc}` : '';
        return `<span class="segment${segment.completed === false ? ' pending' : ''}${segment.contested ? ' contested' : ''}">${escapeHtml(segment.text)}</span><span class="latency">${state} ${formatLatency(segment.latencyMs)}${csrc}</span>`;
      }).join(' ');
      return `<section class="turn"><div class="turn-head"><span class="speaker" style="color:${colorFor(name)}">${escapeHtml(name)}</span><span class="turn-time">${formatClock(group.startTimeSeconds + cutStartSec)}</span></div><div class="turn-body">${rows}</div></section>`;
    }).join('') : '<div class="empty"><strong>Listening…</strong><span>The actual pipeline is running; waiting for its first update.</span></div>';
    firstVisible.textContent = latestFirstVisibleMs === null ? '—' : `${formatLatency(latestFirstVisibleMs)} · target ≤4.0s`;
    firstVisible.className = `metric-value${latestFirstVisibleMs === null ? '' : latestFirstVisibleMs <= 4000 ? ' good' : ' warn'}`;
    updateLatency.textContent = latest ? `${latest.completed ? 'final' : 'draft'} ${formatLatency(latest.latencyMs)}` : '—';
    const pendingRows = [...manager.getState().pendingBySpeaker.values()].reduce((sum, rows) => sum + rows.length, 0);
    stateCount.textContent = `${manager.getState().confirmed.size} confirmed · ${pendingRows} pending · ${activeContests.size / 2} contested`;
    requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }));
  };
  const followClock = () => {
    if (startedAtWallMs !== null) {
      const elapsed = Math.max(0, Math.min(durationSec, (Date.now() - startedAtWallMs) / 1000));
      position.textContent = `${formatClock(cutStartSec + elapsed)} / ${formatClock(cutStartSec + durationSec)}`;
    }
    raf = requestAnimationFrame(followClock);
  };

  const stream = new EventSource(safeResourceUrl(streamUrl));
  stream.onmessage = (message) => {
    const payload = JSON.parse(message.data);
    if (payload.type === 'started') {
      startedAtWallMs = Number(payload.startedAtWallMs);
      const waitMs = Math.max(0, startedAtWallMs - Date.now());
      setTimeout(() => {
        void audio.play();
        livePill.classList.add('running');
        liveLabel.textContent = 'LIVE PIPELINE';
        audioState.textContent = 'Listening live';
      }, waitMs);
      return;
    }
    if (payload.type === 'segment') {
      const event = { ...payload.segment, emittedAtMs: Number(payload.emittedAtFixtureMs), sequence: Number(payload.sequence) };
      received.push(event);
      if (event.completed && event.text.trim()) confirmedIds.add(String(event.segmentId));
      apply(event);
      if (activateAvailableContests(event.segmentId)) rebuild();
      render();
      return;
    }
    if (payload.type === 'complete') {
      livePill.classList.remove('running');
      liveLabel.textContent = 'Complete';
      audioState.textContent = `${payload.cachedCalls}/${payload.calls} cached Whisper calls`;
      return;
    }
    if (payload.type === 'pipeline-error' || payload.type === 'failed') {
      livePill.classList.remove('running');
      liveLabel.textContent = 'Failed';
      audioState.textContent = payload.message;
    }
  };
  stream.onerror = () => { if (liveLabel.textContent === 'Ready') liveLabel.textContent = 'Connecting…'; };
  start.addEventListener('click', async () => {
    start.disabled = true;
    start.textContent = 'Pipeline starting…';
    audio.currentTime = 0;
    const response = await fetch(safeResourceUrl(startUrl));
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.message || `${startUrl}: HTTP ${response.status}`);
    }
    start.textContent = 'Call in progress';
  });
  raf = requestAnimationFrame(followClock);
  window.addEventListener('beforeunload', () => { stream.close(); if (raf) cancelAnimationFrame(raf); });
}

export async function bootTeamsPipelineLive(root = document.getElementById('app')) {
  const params = new URLSearchParams(location.search);
  const dataUrl = safeResourceUrl(params.get('data') || '/result-live.json');
  const audioUrl = safeResourceUrl(params.get('audio') || '/audio.wav', { allowBlob: true });
  const backend = safeResourceUrl(params.get('backend') || 'http://127.0.0.1:8771');
  const reference = await fetch(dataUrl).then((response) => {
    if (!response.ok) throw new Error(`${dataUrl}: HTTP ${response.status}`);
    return response.json();
  });
  mountTeamsPipelineLive(root, reference, {
    audioUrl,
    streamUrl: new URL('/events', backend).href,
    startUrl: new URL('/start', backend).href,
  });
}

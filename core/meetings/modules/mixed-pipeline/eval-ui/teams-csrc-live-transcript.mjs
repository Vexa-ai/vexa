import { createTranscriptManager, groupSegments } from '/_shared/transcript-rendering.js';
import { buildContestPlan, toTranscriptSegment } from './teams-csrc-live-model.mjs';
import { safeResourceUrl } from './safe-resource-url.mjs';

const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[character]);
const formatClock = (seconds) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;
const formatLatency = (milliseconds) => Number.isFinite(milliseconds) ? `+${(milliseconds / 1000).toFixed(1)}s` : '—';

const speakerColors = ['#58a6ff', '#3fb950', '#d29922', '#db61a2', '#a371f7', '#f0883e', '#39c5cf', '#ff7b72'];
const speakerColor = (name) => {
  let hash = 0;
  for (let index = 0; index < name.length; index++) hash = (hash * 31 + name.charCodeAt(index)) | 0;
  return speakerColors[Math.abs(hash) % speakerColors.length];
};

export function mountTeamsLiveTranscript(root, result, { audioUrl, dataUrl } = {}) {
  if (!result || result.kind !== 'teams-csrc-gmeet-window-fixture-eval') {
    throw new Error('expected teams-csrc-gmeet-window-fixture-eval JSON');
  }
  if (!Array.isArray(result.candidate?.events)) {
    throw new Error('candidate.events is missing; rerun teams-csrc-fixture-eval with the emission-tape build');
  }

  const cutStartMs = finite(result.slice?.cutStartMs);
  const cutStartSec = finite(result.slice?.startSec);
  const durationSec = finite(result.slice?.durationSec);
  const events = result.candidate.events.slice().sort((left, right) =>
    finite(left.emittedAtMs) - finite(right.emittedAtMs) || finite(left.sequence) - finite(right.sequence));
  const plans = buildContestPlan(result);
  const actions = [
    ...events.map((event) => ({ kind: 'event', atMs: finite(event.emittedAtMs), order: 0, event })),
    ...plans.filter((plan) => Number.isFinite(plan.decisionAtMs))
      .map((plan) => ({ kind: 'contest', atMs: plan.decisionAtMs, order: 1, plan })),
  ].sort((left, right) => left.atMs - right.atMs || left.order - right.order);

  root.innerHTML = `
    <header>
      <div class="topbar">
        <span class="brand">Vexa</span>
        <span class="meeting"><span class="platform-dot"></span>Microsoft Teams · fixture m26123</span>
        <span class="live-pill" id="live-pill"><span class="dot"></span><span id="live-label">Ready</span></span>
      </div>
      <div class="transport">
        <audio id="audio" controls preload="metadata"></audio>
        <select id="speed" aria-label="Playback speed"><option value="1">1× real time</option><option value="2">2×</option><option value="4">4×</option></select>
        <button type="button" id="start">Start call replay</button>
      </div>
    </header>
    <section class="metrics" aria-label="Live transcript timing">
      <div class="metric"><div class="metric-label">Call position</div><div class="metric-value" id="position">${formatClock(cutStartSec)} / ${formatClock(cutStartSec + durationSec)}</div></div>
      <div class="metric"><div class="metric-label">Latest first visible</div><div class="metric-value" id="first-visible">—</div></div>
      <div class="metric"><div class="metric-label">Latest transcript update</div><div class="metric-value" id="update-latency">—</div></div>
      <div class="metric"><div class="metric-label">Current state</div><div class="metric-value" id="state-count">0 confirmed · 0 pending</div></div>
    </section>
    <main id="feed"><div class="empty"><strong>Ready to join the call</strong><span>Press Start call replay, then listen and watch the transcript form.</span></div></main>
    <footer hidden>data: ${escapeHtml(dataUrl)}</footer>`;

  const audio = root.querySelector('#audio');
  const feed = root.querySelector('#feed');
  const livePill = root.querySelector('#live-pill');
  const liveLabel = root.querySelector('#live-label');
  const position = root.querySelector('#position');
  const firstVisible = root.querySelector('#first-visible');
  const updateLatency = root.querySelector('#update-latency');
  const stateCount = root.querySelector('#state-count');
  const speed = root.querySelector('#speed');
  const start = root.querySelector('#start');
  audio.src = safeResourceUrl(audioUrl ?? '/audio.wav', { allowBlob: true });

  let manager = createTranscriptManager();
  let actionIndex = 0;
  let lastSyncedSec = 0;
  let processedEvents = [];
  let activeContests = new Map();
  let visibleSegments = [];
  let latestEvent = null;
  let latestFirstVisibleMs = null;
  let raf = null;
  const firstSeen = new Set();

  const contestFor = (segmentId) => activeContests.get(String(segmentId)) ?? null;

  const feedEvent = (event) => {
    const contest = contestFor(event.segmentId);
    const segment = toTranscriptSegment(event, cutStartMs, contest);
    const speaker = segment.speaker;
    const message = segment.completed
      ? { type: 'transcript', speaker, confirmed: [segment], pending: [] }
      : { type: 'transcript', speaker, confirmed: [], pending: segment.text.trim() ? [segment] : [] };
    manager.handleMessage(message);
    visibleSegments = manager.getSegments();
    latestEvent = segment;
    if (segment.text.trim() && !firstSeen.has(segment.segment_id)) {
      firstSeen.add(segment.segment_id);
      latestFirstVisibleMs = segment.latencyMs;
    }
  };

  const rebuild = () => {
    manager = createTranscriptManager();
    visibleSegments = [];
    latestEvent = null;
    firstSeen.clear();
    latestFirstVisibleMs = null;
    for (const event of processedEvents) feedEvent(event);
  };

  const render = () => {
    const groups = groupSegments(visibleSegments);
    if (!groups.length) {
      feed.innerHTML = '<div class="empty"><strong>Listening…</strong><span>Audio is running; waiting for the first transcript update.</span></div>';
    } else {
      feed.innerHTML = groups.map((group) => {
        const name = group.key || 'Speaker';
        const body = group.segments.map((segment) => {
          const state = segment.completed === false ? 'DRAFT' : 'FINAL';
          const latencyClass = !firstSeen.has(`${segment.segment_id}:rendered`) && segment.latencyMs <= 4000 ? ' good' : '';
          const contestClass = segment.contested ? ' contested' : '';
          return `<span class="segment${segment.completed === false ? ' pending' : ''}${contestClass}">${escapeHtml(segment.text)}</span><span class="latency${latencyClass}">${state} ${formatLatency(segment.latencyMs)}</span>`;
        }).join(' ');
        return `<section class="turn"><div class="turn-head"><span class="speaker" style="color:${speakerColor(name)}">${escapeHtml(name)}</span><span class="turn-time">${formatClock(group.startTimeSeconds + cutStartSec)}</span></div><div class="turn-body">${body}</div></section>`;
      }).join('');
    }
    position.textContent = `${formatClock(audio.currentTime + cutStartSec)} / ${formatClock(cutStartSec + durationSec)}`;
    firstVisible.textContent = latestFirstVisibleMs === null ? '—' : `${formatLatency(latestFirstVisibleMs)} · target ≤4.0s`;
    firstVisible.className = `metric-value${latestFirstVisibleMs === null ? '' : latestFirstVisibleMs <= 4000 ? ' good' : ' warn'}`;
    updateLatency.textContent = latestEvent ? `${latestEvent.completed ? 'final' : 'draft'} ${formatLatency(latestEvent.latencyMs)}` : '—';
    const confirmedCount = manager.getState().confirmed.size;
    const pendingCount = manager.getState().pendingBySpeaker.size;
    const activeContestCount = new Set([...activeContests.values()].map((row) => row.index)).size;
    stateCount.textContent = `${confirmedCount} confirmed · ${pendingCount} pending · ${activeContestCount} contested`;
    if (!audio.paused && groups.length) requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }));
  };

  const reset = () => {
    manager = createTranscriptManager();
    actionIndex = 0;
    processedEvents = [];
    activeContests = new Map();
    visibleSegments = [];
    latestEvent = null;
    latestFirstVisibleMs = null;
    firstSeen.clear();
  };

  const processAction = (action) => {
    if (action.kind === 'event') {
      processedEvents.push(action.event);
      feedEvent(action.event);
      return;
    }
    activeContests.set(String(action.plan.segmentId), action.plan);
    activeContests.set(String(action.plan.rivalSegmentId), action.plan);
    rebuild();
  };

  const syncTo = (seconds, forceReset = false) => {
    if (forceReset || seconds + 0.05 < lastSyncedSec) reset();
    const targetMs = cutStartMs + seconds * 1000;
    let changed = forceReset;
    while (actionIndex < actions.length && actions[actionIndex].atMs <= targetMs) {
      processAction(actions[actionIndex++]);
      changed = true;
    }
    lastSyncedSec = seconds;
    if (changed) render();
    else position.textContent = `${formatClock(seconds + cutStartSec)} / ${formatClock(cutStartSec + durationSec)}`;
  };

  const follow = () => {
    syncTo(audio.currentTime);
    if (!audio.paused && !audio.ended) raf = requestAnimationFrame(follow);
  };
  audio.addEventListener('play', () => {
    livePill.classList.add('running');
    liveLabel.textContent = 'LIVE REPLAY';
    start.textContent = 'Restart call';
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(follow);
  });
  audio.addEventListener('pause', () => {
    livePill.classList.remove('running');
    liveLabel.textContent = audio.ended ? 'Complete' : 'Paused';
    if (raf) cancelAnimationFrame(raf);
  });
  audio.addEventListener('ended', () => {
    syncTo(durationSec);
    livePill.classList.remove('running');
    liveLabel.textContent = 'Complete';
  });
  audio.addEventListener('seeking', () => syncTo(audio.currentTime, true));
  speed.addEventListener('change', () => { audio.playbackRate = Number(speed.value); });
  start.addEventListener('click', () => {
    if (audio.currentTime > 0.05 || audio.ended) {
      audio.currentTime = 0;
      syncTo(0, true);
    }
    void audio.play();
  });
  render();
}

export async function bootTeamsLiveTranscript(root = document.getElementById('app')) {
  const params = new URLSearchParams(location.search);
  const dataUrl = safeResourceUrl(params.get('data') || '/result.json');
  const audioUrl = safeResourceUrl(params.get('audio') || '/audio.wav', { allowBlob: true });
  const result = await fetch(dataUrl).then((response) => {
    if (!response.ok) throw new Error(`${dataUrl}: HTTP ${response.status}`);
    return response.json();
  });
  mountTeamsLiveTranscript(root, result, { audioUrl, dataUrl });
}

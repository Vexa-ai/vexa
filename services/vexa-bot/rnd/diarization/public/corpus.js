/**
 * Synthetic corpus browser — fetches /corpus/index.json, then for each entry
 * loads ground-truth.json + harness-output.json and renders side-by-side
 * with an inline HTML5 audio player for the WAV.
 */

const itemsEl = document.getElementById('items');

const palette = [
  'var(--speaker-0)',
  'var(--speaker-1)',
  'var(--speaker-2)',
  'var(--speaker-3)',
  'var(--speaker-4)',
  'var(--speaker-5)',
];

function speakerColor(label) {
  const slot = hashSlot(label, palette.length);
  return palette[slot];
}

function hashSlot(label, mod) {
  // Stable per-label color: speaker_0 → 0, speaker_1 → 1, "host" → 2 etc.
  const m = /^speaker_(\d+)$/.exec(label);
  if (m) return Number(m[1]) % mod;
  let h = 0;
  for (let i = 0; i < label.length; i++) h = ((h << 5) - h + label.charCodeAt(i)) | 0;
  return Math.abs(h) % mod;
}

function fmtTime(ms) {
  const s = ms / 1000;
  const mm = Math.floor(s / 60);
  const ss = (s - mm * 60).toFixed(2).padStart(5, '0');
  return `${mm}:${ss}`;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) return null;
  return res.json();
}

function renderTurn(label, startMs, endMs, text, extra) {
  const row = document.createElement('div');
  row.className = 'turn';
  const t = document.createElement('div');
  t.className = 't';
  t.textContent = `${fmtTime(startMs)} – ${fmtTime(endMs)}`;
  const lbl = document.createElement('div');
  lbl.className = 'lbl';
  lbl.style.background = speakerColor(label);
  lbl.textContent = label;
  const txt = document.createElement('div');
  txt.className = 'txt';
  txt.textContent = text || extra || '';
  if (extra) {
    const small = document.createElement('span');
    small.style.color = '#999';
    small.style.fontFamily = 'ui-monospace, monospace';
    small.style.fontSize = '11px';
    small.textContent = '  ' + extra;
    txt.append(small);
  }
  row.append(t, lbl, txt);
  return row;
}

function renderGroundTruth(parent, gt) {
  for (const turn of gt.turns) {
    parent.append(renderTurn(turn.speaker, turn.start_ms, turn.end_ms, turn.text));
  }
}

function fmtNum(v) {
  // NaN serializes as null in JSON; null/undefined → '--', numbers → fixed
  if (v == null || typeof v !== 'number' || !Number.isFinite(v)) return '--';
  return v.toFixed(3);
}

function renderHarnessOutput(parent, hx) {
  for (const c of hx.commits) {
    const dist = fmtNum(c.centroidDist);
    const turn = fmtNum(c.turnDist);
    const flags = [];
    if (c.isNew) flags.push('NEW');
    if (!c.seedAllowed) flags.push('!seed');
    flags.push(`db=${c.dbSize}`);
    const meta = `c_d=${dist}  t_d=${turn}  ${flags.join(' ')}`;
    parent.append(renderTurn(c.speakerId, c.tStartMs, c.tEndMs, meta));
  }
}

async function renderItem(item, container) {
  const card = document.createElement('div');
  card.className = 'item';
  card.innerHTML = `
    <h2>${item.id}</h2>
    <div class="player"><audio controls preload="metadata" src="${item.wav}"></audio></div>
    <div class="files">
      <a href="${item.wav}" target="_blank">WAV</a>
      ${item.ground_truth ? `<a href="${item.ground_truth}" target="_blank">ground-truth.json</a>` : ''}
      ${item.harness_output ? `<a href="${item.harness_output}" target="_blank">harness-output.json</a>` : ''}
    </div>
    <div class="columns">
      <div class="col gt"><h3>Ground truth</h3><div class="rows" data-role="gt"></div></div>
      <div class="col hx"><h3>Harness output</h3><div class="rows" data-role="hx"></div></div>
    </div>`;
  container.append(card);

  const gtRows = card.querySelector('[data-role="gt"]');
  const hxRows = card.querySelector('[data-role="hx"]');
  const [gt, hx] = await Promise.all([
    item.ground_truth ? fetchJson(item.ground_truth) : null,
    item.harness_output ? fetchJson(item.harness_output) : null,
  ]);
  if (gt) renderGroundTruth(gtRows, gt); else gtRows.innerHTML = '<div class="empty">no ground-truth.json</div>';
  if (hx) renderHarnessOutput(hxRows, hx); else hxRows.innerHTML = '<div class="empty">no harness-output.json — run <code>npm run eval:run -- ' + item.id + '</code></div>';
}

async function main() {
  const index = await fetchJson('/corpus/index.json');
  if (!index || !index.items.length) {
    itemsEl.innerHTML = '<div class="empty">No corpus yet. Run <code>npm run eval:render eval/conversations/&lt;name&gt;.json</code> first.</div>';
    return;
  }
  itemsEl.innerHTML = '';
  for (const item of index.items) {
    await renderItem(item, itemsEl);
  }
}

main().catch((err) => {
  itemsEl.innerHTML = `<div class="empty">error: ${err.message}</div>`;
});

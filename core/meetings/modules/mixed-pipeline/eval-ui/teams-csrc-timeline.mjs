import { detectContestedWords } from './teams-contested-word-detector.mjs';

export const WORD_CONTEST_KIND = 'teams-csrc-word-contest-annotations-v1';

const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[character]);

export function flagContestedWords(row, annotation) {
  const text = String(row.text ?? '').trim();
  if (!annotation?.contestedText) return text;
  const start = text.toLocaleLowerCase().indexOf(String(annotation.contestedText).toLocaleLowerCase());
  if (start < 0) return text;
  const end = start + String(annotation.contestedText).length;
  return `${text.slice(0, start)}[${text.slice(start, end)}]${text.slice(end)}`;
}

export function buildTimelineModel(result, annotationDocument = null) {
  if (!result || result.kind !== 'teams-csrc-gmeet-window-fixture-eval') {
    throw new Error('expected teams-csrc-gmeet-window-fixture-eval JSON');
  }
  if (annotationDocument && annotationDocument.kind !== WORD_CONTEST_KIND) {
    throw new Error(`expected ${WORD_CONTEST_KIND} annotations`);
  }
  const cutStartMs = finite(result.slice?.cutStartMs);
  const durationSec = finite(result.slice?.durationSec);
  if (!(durationSec > 0)) throw new Error('fixture slice.durationSec must be positive');
  const seconds = (epochMs) => Math.max(0, Math.min(durationSec, (finite(epochMs) - cutStartMs) / 1000));
  const detected = detectContestedWords(result);
  const annotations = new Map(detected.rows.map((row) => [String(row.segmentId), { ...row, source: 'automatic' }]));
  for (const row of annotationDocument?.rows ?? []) {
    annotations.set(String(row.segmentId), { ...row, source: 'manual' });
  }

  const spans = (result.candidate?.spans ?? []).map((span) => ({
    csrc: finite(span.csrc), startSec: seconds(span.startMs), endSec: seconds(span.endMs),
  }));
  const acceptedSpans = (result.candidate?.acceptedSpans ?? []).map((span) => ({
    csrc: finite(span.csrc), startSec: seconds(span.startMs), endSec: seconds(span.endMs),
  }));
  const confirmed = (result.candidate?.confirmed ?? []).map((row) => ({
    ...row,
    csrc: finite(row.csrc),
    segmentId: String(row.segmentId),
    startSec: seconds(row.startMs),
    endSec: seconds(row.endMs),
  }));
  const pending = (result.candidate?.pending ?? []).map((row) => ({
    ...row,
    csrc: finite(row.csrc),
    segmentId: String(row.segmentId),
    startSec: seconds(row.startMs),
    endSec: seconds(row.endMs),
  }));
  const singlePass = (result.singlePass?.segments ?? []).map((row) => ({
    text: String(row.text ?? ''),
    startSec: Math.max(0, Math.min(durationSec, finite(row.start))),
    endSec: Math.max(0, Math.min(durationSec, finite(row.end))),
  }));
  const tracks = [...new Set([
    ...spans.map((row) => row.csrc),
    ...acceptedSpans.map((row) => row.csrc),
    ...confirmed.map((row) => row.csrc),
    ...pending.map((row) => row.csrc),
  ])].sort((a, b) => a - b);
  const confirmedById = new Map(confirmed.map((row) => [row.segmentId, row]));
  const contests = [...annotations.values()].map((annotation) => {
    const left = confirmedById.get(String(annotation.segmentId));
    const right = confirmedById.get(String(annotation.rivalSegmentId));
    return { annotation, left, right };
  }).filter((row) => row.left && row.right);

  return {
    durationSec,
    cutStartSec: finite(result.slice?.startSec),
    spans,
    acceptedSpans,
    confirmed,
    displayedConfirmed: confirmed,
    pending,
    singlePass,
    tracks,
    annotations,
    contests,
    detectionReceipt: detected.receipt,
    health: result.candidate?.health ?? {},
    refreshLatency: result.candidate?.refreshLatency ?? {},
  };
}

const svgElement = (name, attrs = {}, text = '') => {
  const element = document.createElementNS('http://www.w3.org/2000/svg', name);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
  if (text) element.textContent = text;
  return element;
};

const fmt = (seconds) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;

export function mountTeamsCsrcTimeline(root, model, { audioUrl, dataUrl, annotationsUrl } = {}) {
  const contestedRows = model.contests.map(({ annotation, left, right }) => ({
    row: left, rivalRow: right, annotation,
  }));
  const contestForSegment = (segmentId) => model.contests.find(({ left, right }) => left.segmentId === segmentId || right.segmentId === segmentId) ?? null;
  const contestAnnotationFor = (row, contest) => contest ? {
    contestedText: contest.annotation.contestedText,
    rivalCsrc: contest.left.segmentId === row.segmentId ? contest.right.csrc : contest.left.csrc,
  } : null;
  root.innerHTML = `
    <header>
      <div>
        <h1>Teams CSRC · audio-aligned transcript replay</h1>
        <p>Meeting ${fmt(model.cutStartSec)}–${fmt(model.cutStartSec + model.durationSec)} · dashed = raw CSRC · fill = routed audio · lower bar = confirmed transcript · [words] = contested wording</p>
      </div>
      <div class="receipt" id="receipt"></div>
    </header>
    <audio id="audio" controls preload="metadata"></audio>
    <section class="word-contests"${contestedRows.length ? '' : ' hidden'}>
      <h2>Contested words · ownership unresolved</h2>
      <p>Both CSRC rows remain. Only the shared words are wrapped as [words]; no winner is inferred. Click a card to listen.</p>
      <div class="word-contest-list">
        ${contestedRows.map(({ row, rivalRow, annotation }) => `
          <button type="button" class="contested-card" data-contested-seek="${row.startSec}" data-segment-id="${escapeHtml(row.segmentId)}">
            <span class="word-contest-meta">${fmt(row.startSec)} · duplicated phrase: “${escapeHtml(annotation.contestedText)}”</span>
            <span class="word-contest-party"><b>CONTESTED — CSRC ${row.csrc}</b>${escapeHtml(flagContestedWords(row, { contestedText: annotation.contestedText }))}</span>
            <span class="word-contest-party"><b>CONTESTED — CSRC ${rivalRow.csrc}</b>${escapeHtml(flagContestedWords(rivalRow, { contestedText: annotation.contestedText }))}</span>
            <span class="word-contest-context"><b>DETECTED BY</b>${escapeHtml(annotation.reason)}</span>
            ${annotation.evidence ? `<span class="word-contest-evidence">${annotation.evidence.sharedRoutedMs} ms shared audio · ${annotation.evidence.matchedTokens} matching words · ${annotation.evidence.medianWordDeltaMs} ms median word-time delta</span>` : ''}
          </button>`).join('')}
      </div>
    </section>
    <svg id="chart" role="img" aria-label="Audio-aligned raw and routed CSRC activity, confirmed transcripts, and single-pass reference"></svg>
    <div class="legend" id="legend"></div>
    <div class="tooltip" id="tooltip" role="tooltip"></div>
    <footer>data: <code>${escapeHtml(dataUrl)}</code>${annotationsUrl ? ` · annotations: <code>${escapeHtml(annotationsUrl)}</code>` : ''}</footer>`;

  const svg = root.querySelector('#chart');
  const audio = root.querySelector('#audio');
  const tip = root.querySelector('#tooltip');
  const legend = root.querySelector('#legend');
  const receipt = root.querySelector('#receipt');
  audio.src = safeResourceUrl(audioUrl ?? '/audio.wav', { allowBlob: true });

  const palette = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)'];
  const left = 86, right = 14, top = 34, laneHeight = 50, singleHeight = 42, bottom = 34;
  const overlap = (leftRow, rightRow) => leftRow.endSec >= rightRow.startSec && leftRow.startSec <= rightRow.endSec;

  receipt.textContent = `${model.confirmed.length} confirmed · ${model.pending.length} pending · ${model.tracks.length} CSRCs · ${model.detectionReceipt.annotations} contested pairs`;

  const showTip = (event, text) => {
    tip.textContent = text;
    tip.style.display = 'block';
    const rootBox = root.getBoundingClientRect();
    const tipBox = tip.getBoundingClientRect();
    tip.style.left = `${Math.min(rootBox.width - tipBox.width - 8, Math.max(8, event.clientX - rootBox.left + 12))}px`;
    tip.style.top = `${Math.max(8, event.clientY - rootBox.top - tipBox.height - 10)}px`;
  };
  const hideTip = () => { tip.style.display = 'none'; };
  const seek = (seconds) => {
    audio.currentTime = Math.max(0, Math.min(model.durationSec - 0.01, seconds));
    void audio.play().catch(() => undefined);
  };
  root.querySelectorAll('[data-contested-seek]').forEach((button) => {
    button.addEventListener('click', () => seek(Number(button.dataset.contestedSeek)));
  });

  const addTooltip = (element, detail, startSec, y) => {
    element.addEventListener('pointermove', (event) => showTip(event, detail));
    element.addEventListener('pointerleave', hideTip);
    element.addEventListener('focus', () => {
      const box = root.getBoundingClientRect();
      showTip({ clientX: box.left + 100, clientY: box.top + y }, detail);
    });
    element.addEventListener('blur', hideTip);
    element.addEventListener('click', (event) => { event.stopPropagation(); seek(startSec); });
  };

  const draw = () => {
    const width = Math.max(520, root.clientWidth - 16);
    const plotWidth = width - left - right;
    const height = top + model.tracks.length * laneHeight + singleHeight + bottom;
    const x = (seconds) => left + Math.max(0, Math.min(model.durationSec, seconds)) / model.durationSec * plotWidth;
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.innerHTML = '';
    svg.appendChild(svgElement('rect', { class: 'frame', x: left, y: 20, width: plotWidth, height: height - bottom - 10 }));

    const gridStep = model.durationSec <= 180 ? 10 : model.durationSec <= 900 ? 30 : 60;
    for (let second = 0; second <= model.durationSec; second += gridStep) {
      svg.appendChild(svgElement('line', { class: 'grid', x1: x(second), x2: x(second), y1: 20, y2: height - bottom }));
      svg.appendChild(svgElement('text', { class: 'axis', x: x(second), y: 13, 'text-anchor': second === 0 ? 'start' : second === model.durationSec ? 'end' : 'middle' }, fmt(second)));
    }

    model.tracks.forEach((track, lane) => {
      const color = palette[lane % palette.length];
      const y = top + lane * laneHeight;
      svg.appendChild(svgElement('text', { class: 'track-label', x: 4, y: y + 20 }, `CSRC ${track}`));
      svg.appendChild(svgElement('line', { class: 'base', x1: left, x2: width - right, y1: y + 18, y2: y + 18 }));

      model.spans.filter((span) => span.csrc === track).forEach((span) => {
        const bar = svgElement('rect', {
          class: 'raw-span', x: x(span.startSec), y: y + 3,
          width: Math.max(2, x(span.endSec) - x(span.startSec)), height: 30, rx: 2,
          stroke: color, tabindex: 0,
        });
        addTooltip(bar, `RAW CSRC ${track}  ${fmt(span.startSec)}–${fmt(span.endSec)}\nactive:true → active:false`, span.startSec, y);
        svg.appendChild(bar);
      });

      model.acceptedSpans.filter((span) => span.csrc === track).forEach((span) => {
        const confirmed = model.displayedConfirmed.filter((row) => row.csrc === track && overlap(row, span) && row.text.trim());
        const oracle = model.singlePass.filter((row) => overlap(row, span) && row.text.trim());
        const gap = confirmed.length === 0 && oracle.length > 0;
        const bar = svgElement('rect', {
          class: `accepted-span${gap ? ' gap' : ''}`, x: x(span.startSec), y: y + 8,
          width: Math.max(2, x(span.endSec) - x(span.startSec)), height: 14, rx: 2,
          fill: `color-mix(in srgb, ${color} 34%, transparent)`, stroke: color, tabindex: 0,
        });
        const detail = confirmed.length
          ? confirmed.map((row) => {
              const contest = contestForSegment(row.segmentId);
              const text = flagContestedWords(row, contestAnnotationFor(row, contest));
              return `${fmt(row.startSec)}  ${text}`;
            }).join('\n')
          : gap
            ? `CANDIDATE GAP ON THIS CSRC\nSingle pass: ${oracle.map((row) => `${fmt(row.startSec)}  ${row.text.trim()}`).join('\n')}`
            : 'No confirmed or single-pass speech overlaps this routed fragment.';
        addTooltip(bar, `ACCEPTED CSRC ${track}  ${fmt(span.startSec)}–${fmt(span.endSec)}\n${detail}`, span.startSec, y);
        svg.appendChild(bar);

        confirmed.filter((row) => !contestForSegment(row.segmentId)).forEach((row) => {
          svg.appendChild(svgElement('rect', {
            class: 'candidate',
            x: x(Math.max(span.startSec, row.startSec)), y: y + 31,
            width: Math.max(1, x(Math.min(span.endSec, Math.max(row.endSec, row.startSec + 0.2))) - x(Math.max(span.startSec, row.startSec))),
            height: 5, rx: 1, fill: color,
          }));
        });
      });

      model.displayedConfirmed.filter((row) => row.csrc === track && contestForSegment(row.segmentId)).forEach((row) => {
        const contest = contestForSegment(row.segmentId);
        const annotation = contestAnnotationFor(row, contest);
        const bar = svgElement('rect', {
          class: 'candidate word-contest', x: x(row.startSec), y: y + 31,
          width: Math.max(2, x(Math.max(row.endSec, row.startSec + 0.2)) - x(row.startSec)),
          height: 5, rx: 1, fill: color, tabindex: 0,
          'data-segment-id': row.segmentId, 'data-contested-with': annotation.rivalCsrc,
        });
        addTooltip(
          bar,
          `CONTESTED WORDS BETWEEN CSRC ${row.csrc} AND CSRC ${annotation.rivalCsrc}\n${fmt(row.startSec)}  ${flagContestedWords(row, annotation)}\n\n${contest.annotation.reason ?? ''}`,
          row.startSec,
          y,
        );
        svg.appendChild(bar);
      });

      model.pending.filter((row) => row.csrc === track && row.text.trim()).forEach((row) => {
        const bar = svgElement('rect', {
          class: 'pending', x: x(row.startSec), y: y + 38,
          width: Math.max(2, x(Math.max(row.endSec, row.startSec + 0.2)) - x(row.startSec)),
          height: 4, rx: 1, stroke: color, tabindex: 0,
        });
        addTooltip(bar, `PENDING CSRC ${track}\n${fmt(row.startSec)}  ${row.text.trim()}`, row.startSec, y);
        svg.appendChild(bar);
      });
    });

    const singleY = top + model.tracks.length * laneHeight + 5;
    svg.appendChild(svgElement('text', { class: 'track-label', x: 4, y: singleY + 17 }, 'SINGLE'));
    svg.appendChild(svgElement('line', { class: 'base', x1: left, x2: width - right, y1: singleY + 14, y2: singleY + 14 }));
    model.singlePass.forEach((segment) => {
      const bar = svgElement('rect', {
        class: 'single', x: x(segment.startSec), y: singleY + 3,
        width: Math.max(2, x(segment.endSec) - x(segment.startSec)), height: 22, rx: 2, tabindex: 0,
      });
      addTooltip(bar, `SINGLE PASS  ${fmt(segment.startSec)}–${fmt(segment.endSec)}\n${segment.text.trim()}`, segment.startSec, singleY);
      svg.appendChild(bar);
    });

    const cursor = svgElement('line', { class: 'cursor', x1: x(audio.currentTime), x2: x(audio.currentTime), y1: 20, y2: height - bottom, 'data-cursor': 'true' });
    svg.appendChild(cursor);
    svg.onclick = (event) => {
      if (event.target !== svg && event.target.getAttribute('class') !== 'frame') return;
      const box = svg.getBoundingClientRect();
      const logicalX = (event.clientX - box.left) / box.width * width;
      seek((logicalX - left) / plotWidth * model.durationSec);
    };
    svg.onpointermove = (event) => {
      if (event.target !== svg && event.target.getAttribute('class') !== 'frame') return;
      const box = svg.getBoundingClientRect();
      const logicalX = (event.clientX - box.left) / box.width * width;
      const seconds = Math.max(0, Math.min(model.durationSec, (logicalX - left) / plotWidth * model.durationSec));
      showTip(event, `Seek ${fmt(seconds)} · meeting ${fmt(seconds + model.cutStartSec)}`);
    };
  };

  legend.innerHTML = model.tracks.map((track, index) => `<span><i style="background:${palette[index % palette.length]}"></i>CSRC ${track}</span>`).join('')
    + '<span>outline = raw</span><span>fill = routed audio</span><span>lower = confirmed</span>'
    + '<span>orange dashed = contested; both rows kept</span>'
    + '<span>[words] = contested wording</span><span><i style="background:var(--series-5)"></i>single-pass</span>';
  audio.addEventListener('timeupdate', () => {
    const cursor = svg.querySelector('[data-cursor="true"]');
    if (!cursor) return;
    const width = Number(svg.viewBox.baseVal.width);
    const px = left + Math.max(0, Math.min(model.durationSec, audio.currentTime)) / model.durationSec * (width - left - right);
    cursor.setAttribute('x1', px); cursor.setAttribute('x2', px);
  });
  new ResizeObserver(draw).observe(root);
  draw();
}

export async function bootTeamsCsrcTimeline(root = document.getElementById('app')) {
  const params = new URLSearchParams(location.search);
  const dataUrl = safeResourceUrl(params.get('data') || '/result.json');
  const audioUrl = safeResourceUrl(params.get('audio') || '/audio.wav', { allowBlob: true });
  const annotationsParam = params.get('annotations');
  const annotationsUrl = annotationsParam ? safeResourceUrl(annotationsParam) : null;
  const [result, annotations] = await Promise.all([
    fetch(dataUrl).then((response) => {
      if (!response.ok) throw new Error(`${dataUrl}: HTTP ${response.status}`);
      return response.json();
    }),
    annotationsUrl
      ? fetch(annotationsUrl).then((response) => {
          if (!response.ok) throw new Error(`${annotationsUrl}: HTTP ${response.status}`);
          return response.json();
        })
      : null,
  ]);
  mountTeamsCsrcTimeline(root, buildTimelineModel(result, annotations), {
    audioUrl, dataUrl, annotationsUrl,
  });
}
import { safeResourceUrl } from './safe-resource-url.mjs';

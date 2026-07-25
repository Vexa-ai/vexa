/**
 * #797 authored Zoom producer_dom_trace.v1 replay.
 *
 * The fixture contains only canonical selector outcomes and fixed pseudonyms.
 * It drives the real createZoomSpeakers poller with its production 250ms /
 * two-poll contract. It is authored coverage, not a claim of live DOM capture.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  parseZoomProducerDomTrace,
  ZOOM_TRACE_MAX_ROWS,
  type ZoomProducerDomTrace,
  type ZoomProducerDomTraceAdmissionCode,
  type ZoomProducerDomTraceRow,
  type ZoomTracePseudonym,
} from './producer-dom-trace.js';
import {
  createZoomSpeakers,
  type ZoomNameUnresolvedObservation,
} from './zoom-speakers.js';

interface ReplayChange {
  atMs: number;
  participant: ZoomTracePseudonym | null;
}

interface ReplayUnresolved {
  atMs: number;
  type: 'name-unresolved';
  platform: 'zoom';
  signal: 'dom-active';
  reason: ZoomNameUnresolvedObservation['reason'];
}

interface ReplayResult {
  schema: 'producer_dom_trace.result.v1';
  platform: 'zoom';
  provenance: 'authored' | 'captured';
  changes: ReplayChange[];
  unresolved: ReplayUnresolved[];
  finalActive: ZoomTracePseudonym | null;
}

const FIXTURE_URL = new URL('./fixtures/zoom-producer-dom-trace.v1.authored.jsonl', import.meta.url);
const FIXED_EPOCH_MS = 1_700_000_000_000;

let intervalCallback: (() => void) | null = null;
let currentRow: ZoomProducerDomTraceRow;
let currentAtMs = 0;

const footer = {
  get innerText(): string {
    return currentRow.footer === 'named' ? currentRow.participant : '   ';
  },
  querySelector: (selector: string) =>
    selector === 'span'
      ? {
          get textContent(): string {
            return currentRow.footer === 'named' ? currentRow.participant : '   ';
          },
        }
      : null,
};

const activeContainer = {
  querySelector: (selector: string) => {
    if (selector !== '.video-avatar__avatar-footer') return null;
    if (currentRow.footer === 'read-fault') throw new Error('authored footer read fault');
    return currentRow.footer === 'absent' ? null : footer;
  },
};

const VIEW_SELECTORS: Record<Exclude<ZoomProducerDomTraceRow['view'], 'none'>, string> = {
  'speaker-active': '.speaker-active-container__video-frame',
  'speaker-bar-active': '.speaker-bar-container__video-frame--active',
  'single-main-active': '.single-main-container__video-frame',
};

function installDocument(): void {
  (globalThis as { document?: unknown }).document = {
    visibilityState: 'visible',
    querySelector: (selector: string) => {
      if (currentRow.footer === 'read-fault') throw new Error('authored DOM read fault');
      if (currentRow.view === 'none') return null;
      return selector === VIEW_SELECTORS[currentRow.view] ? activeContainer : null;
    },
    querySelectorAll: () => [],
  };
}

function replay(trace: ZoomProducerDomTrace): ReplayResult {
  intervalCallback = null;
  const first = trace.rows[0];
  if (!first) throw new Error('C797_ZOOM_TRACE_EMPTY: admitted trace has no rows');
  currentRow = first;
  currentAtMs = first.atMs;
  installDocument();

  const globalObject = globalThis as typeof globalThis & Record<string, unknown>;
  const previousSetInterval = globalThis.setInterval;
  const previousClearInterval = globalThis.clearInterval;
  const previousDateNow = Date.now;
  globalObject.setInterval = (callback: () => void, pollMs?: number) => {
    if (pollMs !== 250) {
      throw new Error(`C797_ZOOM_POLL_CONTRACT: expected 250ms, received ${String(pollMs)}`);
    }
    intervalCallback = callback;
    return 1;
  };
  globalObject.clearInterval = () => {};
  Date.now = () => FIXED_EPOCH_MS + currentAtMs;

  const changes: ReplayChange[] = [];
  const unresolved: ReplayUnresolved[] = [];
  try {
    const watcher = createZoomSpeakers({
      pollMs: trace.header.pollMs,
      onSpeakerChange: (participant) => {
        changes.push({
          atMs: currentAtMs,
          participant: participant as ZoomTracePseudonym | null,
        });
      },
      onNameUnresolved: (observation) => {
        if (observation.tMs !== FIXED_EPOCH_MS + currentAtMs) {
          throw new Error(
            `C797_ZOOM_TRACE_TIME: callback timestamp ${observation.tMs} did not follow row ${currentAtMs}`,
          );
        }
        unresolved.push({
          atMs: currentAtMs,
          type: observation.type,
          platform: observation.platform,
          signal: observation.signal,
          reason: observation.reason,
        });
      },
    });

    for (const row of trace.rows.slice(1)) {
      currentRow = row;
      currentAtMs = row.atMs;
      installDocument();
      if (!intervalCallback) throw new Error('C797_ZOOM_TRACE_CLOCK: watcher did not install its poll');
      intervalCallback();
    }
    const finalActive = watcher.getActiveSpeaker() as ZoomTracePseudonym | null;
    watcher.destroy();
    return {
      schema: 'producer_dom_trace.result.v1',
      platform: 'zoom',
      provenance: trace.header.provenance,
      changes,
      unresolved,
      finalActive,
    };
  } finally {
    globalObject.setInterval = previousSetInterval;
    globalObject.clearInterval = previousClearInterval;
    Date.now = previousDateNow;
  }
}

function expectAdmissionCode(
  jsonl: string,
  expected: ZoomProducerDomTraceAdmissionCode,
  privateMarker?: string,
): void {
  try {
    parseZoomProducerDomTrace(jsonl);
  } catch (error) {
    if (
      typeof error === 'object'
      && error !== null
      && 'code' in error
      && error.code === expected
    ) {
      if (
        privateMarker !== undefined
        && error instanceof Error
        && error.message.includes(privateMarker)
      ) {
        throw new Error('C797_ZOOM_TRACE_PRIVACY: admission error echoed private input');
      }
      return;
    }
    throw new Error(
      `C797_ZOOM_TRACE_ADMISSION: expected ${expected}, got ${String(error)}`,
    );
  }
  throw new Error(`C797_ZOOM_TRACE_ADMISSION: expected ${expected}, trace admitted`);
}

const fixtureText = readFileSync(fileURLToPath(FIXTURE_URL), 'utf8');
const trace = parseZoomProducerDomTrace(fixtureText);
if (trace.header.provenance !== 'authored' || trace.header.pollMs !== 250) {
  throw new Error(`C797_ZOOM_TRACE_PROVENANCE: ${JSON.stringify(trace.header)}`);
}

const capturedText = fixtureText.replace(
  '"provenance":"authored"',
  '"provenance":"captured"',
);
const capturedTrace = parseZoomProducerDomTrace(capturedText);
if (capturedTrace.header.provenance !== 'captured') {
  throw new Error(
    `C797_ZOOM_TRACE_CAPTURED_ADMISSION: ${JSON.stringify(capturedTrace.header)}`,
  );
}
const capturedReplay = replay(capturedTrace);
if (capturedReplay.provenance !== 'captured') {
  throw new Error(
    `C797_ZOOM_TRACE_CAPTURED_RELABEL: captured replay became ${capturedReplay.provenance}`,
  );
}

// Validate every record, including a schema-invalid second observation.
const lines = fixtureText.trimEnd().split('\n');
const invalidSecond = [...lines];
invalidSecond[2] = '{"atMs":250,"view":"speaker-active","footer":"named","participant":"speaker-a","text":"raw"}';
expectAdmissionCode(`${invalidSecond.join('\n')}\n`, 'raw-field');

for (const rawKey of ['dom', 'text', 'class', 'aria', 'title', 'URL']) {
  const invalidRaw = [...lines];
  invalidRaw[1] =
    `{"atMs":0,"view":"speaker-active","footer":"named",` +
    `"participant":"speaker-a","${rawKey}":"forbidden"}`;
  expectAdmissionCode(`${invalidRaw.join('\n')}\n`, 'raw-field');
}

const unknown = [...lines];
unknown[1] = '{"atMs":0,"view":"speaker-active","footer":"named","participant":"speaker-a","extra":true}';
expectAdmissionCode(`${unknown.join('\n')}\n`, 'unknown-field');

const privateUnknown = [...lines];
privateUnknown[1] = '{"atMs":0,"view":"speaker-active","footer":"named","participant":"speaker-a","must-never-cross":true}';
expectAdmissionCode(
  `${privateUnknown.join('\n')}\n`,
  'unknown-field',
  'must-never-cross',
);

const rawName = [...lines];
rawName[1] = '{"atMs":0,"view":"speaker-active","footer":"named","participant":"Alice Example"}';
expectAdmissionCode(`${rawName.join('\n')}\n`, 'invalid-pseudonym');

const duplicateParticipant = [...lines];
duplicateParticipant[1] =
  '{"atMs":0,"view":"speaker-active","footer":"named",'
  + '"participant":"must-never-cross","participant":"speaker-a"}';
expectAdmissionCode(
  `${duplicateParticipant.join('\n')}\n`,
  'invalid-record',
  'must-never-cross',
);

const duplicateProvenance = [...lines];
duplicateProvenance[0] = duplicateProvenance[0]!.replace(
  '"provenance":"authored"',
  '"provenance":"captured","provenance":"authored"',
);
expectAdmissionCode(`${duplicateProvenance.join('\n')}\n`, 'invalid-record');

const epoch = [...lines];
epoch[1] = '{"atMs":1700000000000,"view":"speaker-active","footer":"named","participant":"speaker-a"}';
expectAdmissionCode(`${epoch.join('\n')}\n`, 'time-not-relative');

const nonmonotonic = [...lines];
nonmonotonic[2] = '{"atMs":0,"view":"speaker-active","footer":"named","participant":"speaker-a"}';
expectAdmissionCode(`${nonmonotonic.join('\n')}\n`, 'time-nonmonotonic');

const offCadence = [...lines];
offCadence[2] = '{"atMs":251,"view":"speaker-active","footer":"named","participant":"speaker-a"}';
expectAdmissionCode(`${offCadence.join('\n')}\n`, 'time-off-cadence');

const wrongPollMs = [...lines];
wrongPollMs[0] = wrongPollMs[0]!.replace('"pollMs":250', '"pollMs":251');
expectAdmissionCode(`${wrongPollMs.join('\n')}\n`, 'invalid-header');

const wrongConfirmPolls = [...lines];
wrongConfirmPolls[0] = wrongConfirmPolls[0]!.replace(
  '"confirmPolls":2',
  '"confirmPolls":3',
);
expectAdmissionCode(`${wrongConfirmPolls.join('\n')}\n`, 'invalid-header');

const invalidView = [...lines];
invalidView[1] = '{"atMs":0,"view":"gallery","footer":"named","participant":"speaker-a"}';
expectAdmissionCode(`${invalidView.join('\n')}\n`, 'invalid-enum');

const invalidState = [...lines];
invalidState[1] = '{"atMs":0,"view":"none","footer":"empty"}';
expectAdmissionCode(`${invalidState.join('\n')}\n`, 'invalid-state');

const maximumRows = [
  lines[0],
  ...Array.from(
    { length: ZOOM_TRACE_MAX_ROWS },
    (_, index) =>
      JSON.stringify({
        atMs: index * 250,
        view: 'speaker-active',
        footer: 'named',
        participant: 'speaker-a',
      }),
  ),
];
const maximumTrace = parseZoomProducerDomTrace(`${maximumRows.join('\n')}\n`);
if (maximumTrace.rows.length !== ZOOM_TRACE_MAX_ROWS) {
  throw new Error(
    `C797_ZOOM_TRACE_BOUNDARY: exact ${ZOOM_TRACE_MAX_ROWS}-row trace was not admitted`,
  );
}
const excessiveRows = [
  ...maximumRows,
  JSON.stringify({
    atMs: ZOOM_TRACE_MAX_ROWS * 250,
    view: 'speaker-active',
    footer: 'named',
    participant: 'speaker-a',
  }),
];
expectAdmissionCode(`${excessiveRows.join('\n')}\n`, 'row-limit');

const first = replay(trace);
const second = replay(trace);
const firstBytes = JSON.stringify(first);
const secondBytes = JSON.stringify(second);
if (firstBytes !== secondBytes) {
  throw new Error(`C797_ZOOM_TRACE_DETERMINISM: ${firstBytes} != ${secondBytes}`);
}

const namedChanges = first.changes.filter(
  (change): change is ReplayChange & { participant: ZoomTracePseudonym } =>
    change.participant !== null,
);
if (
  namedChanges.length !== 3
  || namedChanges[0]?.atMs !== 250
  || namedChanges[0]?.participant !== 'speaker-a'
  || namedChanges[1]?.atMs !== 1250
  || namedChanges[1]?.participant !== 'speaker-b'
  || namedChanges[2]?.atMs !== 3250
  || namedChanges[2]?.participant !== 'speaker-a'
) {
  throw new Error(`C797_ZOOM_TRACE_ORDER: ${JSON.stringify(namedChanges)}`);
}
if (first.changes.some((change) => change.atMs === 500 && change.participant === 'speaker-b')) {
  throw new Error(`C797_ZOOM_TRACE_FLICKER: one-poll speaker-b emitted ${firstBytes}`);
}
if (namedChanges.filter((change) => change.atMs <= 250).length !== 1) {
  throw new Error(`C797_ZOOM_TRACE_TWO_POLL: initial held name emitted more than once ${firstBytes}`);
}

const expectedUnresolved: ReplayUnresolved[] = [
  {
    atMs: 1750,
    type: 'name-unresolved',
    platform: 'zoom',
    signal: 'dom-active',
    reason: 'footer-empty',
  },
  {
    atMs: 2750,
    type: 'name-unresolved',
    platform: 'zoom',
    signal: 'dom-active',
    reason: 'footer-absent',
  },
  {
    atMs: 3750,
    type: 'name-unresolved',
    platform: 'zoom',
    signal: 'dom-active',
    reason: 'read-fault',
  },
];
if (JSON.stringify(first.unresolved) !== JSON.stringify(expectedUnresolved)) {
  throw new Error(`C797_ZOOM_TRACE_UNRESOLVED: ${JSON.stringify(first.unresolved)}`);
}
if (first.unresolved.some((observation) => observation.atMs === 2000 || observation.atMs === 2250)) {
  throw new Error(`C797_ZOOM_TRACE_NONE: view=none emitted unresolved ${firstBytes}`);
}
if (
  first.changes.some(
    (change) =>
      change.participant !== null
      && (change.atMs === 1500
        || change.atMs === 1750
        || change.atMs === 2500
        || change.atMs === 2750),
  )
) {
  throw new Error(`C797_ZOOM_TRACE_FABRICATION: unresolved epoch emitted a name ${firstBytes}`);
}
if (first.finalActive !== 'speaker-a') {
  throw new Error(`C797_ZOOM_TRACE_FAULT_MUTATION: final active=${String(first.finalActive)}`);
}

console.log(
  'C797_ZOOM_TRACE_GREEN: sanitized authored/captured admission preserves provenance and row limit; ' +
  'exclusive 250ms/two-poll replay pins order, flicker, empty/absent/fault telemetry, ' +
  'no-view silence, and byte determinism',
);

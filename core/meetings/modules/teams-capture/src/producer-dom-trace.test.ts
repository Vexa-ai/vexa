/**
 * Deterministic Teams producer_dom_trace.v1 replay.
 *
 * The fixture is authored (not captured) and drives the real
 * createTeamsSpeakers state machine through a minimal deterministic DOM/clock.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  createTeamsSpeakers,
  type TeamsNameUnresolvedObservation,
} from './msteams-speakers.js';
import {
  TEAMS_PRODUCER_DOM_TRACE_MAX_RECORDS,
  TEAMS_PRODUCER_DOM_TRACE_MAX_TILES,
  TeamsProducerDomTraceAdmissionError,
  parseTeamsProducerDomTrace,
  serializeTeamsProducerDomTrace,
  type TeamsProducerDomTrace,
  type TeamsProducerDomTraceAdmissionCode,
  type TeamsProducerDomTraceNameToken,
  type TeamsProducerDomTraceTileState,
} from './producer-dom-trace.js';

const FIXTURE_PATH = fileURLToPath(
  new URL('./fixtures/producer-dom-trace.teams.authored.jsonl', import.meta.url),
);
const FIXTURE_TEXT = readFileSync(FIXTURE_PATH, 'utf8');
const VOICE_OUTLINE = '[data-tid="voice-level-stream-outline"]';
const BASE_EPOCH_MS = 1_700_000_000_000;

const DISPLAY_NAMES: Record<TeamsProducerDomTraceNameToken, string> = {
  SPEAKER_A: 'Anna Example',
  SPEAKER_B: 'Boris Example',
  SPEAKER_C: 'Michael Example',
  UNRESOLVED: '',
};

type Timer = {
  id: number;
  atMs: number;
  intervalMs: number | null;
  callback: () => void;
};

class VirtualClock {
  atMs = 0;
  private nextId = 1;
  private readonly timers = new Map<number, Timer>();

  setTimeout(callback: () => void, delayMs = 0): number {
    return this.add(callback, Math.max(0, delayMs), null);
  }

  setInterval(callback: () => void, intervalMs = 0): number {
    return this.add(callback, Math.max(1, intervalMs), Math.max(1, intervalMs));
  }

  requestAnimationFrame(callback: () => void): number {
    return this.add(callback, 16, null);
  }

  clear(id: number): void {
    this.timers.delete(id);
  }

  advanceTo(targetAtMs: number): void {
    if (targetAtMs < this.atMs) throw new Error('virtual clock cannot move backwards');
    while (true) {
      const next = [...this.timers.values()]
        .filter((timer) => timer.atMs <= targetAtMs)
        .sort((a, b) => a.atMs - b.atMs || a.id - b.id)[0];
      if (!next) break;
      this.timers.delete(next.id);
      this.atMs = next.atMs;
      if (next.intervalMs !== null) {
        next.atMs += next.intervalMs;
        this.timers.set(next.id, next);
      }
      next.callback();
    }
    this.atMs = targetAtMs;
  }

  private add(
    callback: () => void,
    delayMs: number,
    intervalMs: number | null,
  ): number {
    const id = this.nextId++;
    this.timers.set(id, {
      id,
      atMs: this.atMs + delayMs,
      intervalMs,
      callback,
    });
    return id;
  }
}

class FakeElement {
  readonly dataset: Record<string, string> = {};
  readonly isConnected = true;
  parentElement: FakeElement | null = null;
  private voiceOutline: FakeElement | null = null;
  private nameSurface: FakeElement | null = null;
  private classes = new Set<string>();

  constructor(
    readonly tagName: string,
    private readonly attrs: Record<string, string> = {},
    readonly textContent = '',
  ) {}

  get classList(): { contains(name: string): boolean } {
    return { contains: (name) => this.classes.has(name) };
  }

  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null;
  }

  apply(
    event: TeamsProducerDomTraceTileState,
    displayNames: Record<TeamsProducerDomTraceNameToken, string>,
  ): void {
    this.nameSurface = event.nameToken === 'UNRESOLVED'
      ? null
      : new FakeElement(
        'SPAN',
        { 'data-tid': 'display-name' },
        displayNames[event.nameToken],
      );
    if (event.signalState === 'absent') {
      this.voiceOutline = null;
      return;
    }
    if (!this.voiceOutline) {
      this.voiceOutline = new FakeElement('DIV', {
        'data-tid': 'voice-level-stream-outline',
      });
      this.voiceOutline.parentElement = this;
    }
    this.voiceOutline.classes = event.voiceState === 'speaking'
      ? new Set(['vdi-frame-occlusion'])
      : new Set();
  }

  querySelector(selector: string): FakeElement | null {
    if (selector === VOICE_OUTLINE) return this.voiceOutline;
    if (selector === '[data-tid*="display-name"]') return this.nameSurface;
    return null;
  }

  querySelectorAll(): FakeElement[] {
    return [];
  }

  matches(): boolean {
    return false;
  }
}

class FakeMutationObserver {
  observe(): void {}
  disconnect(): void {}
}

interface NormalizedHint {
  atMs: number;
  tileId: string;
  name: string;
  edge: 'start' | 'end';
}

interface NormalizedReplay {
  provenance: 'authored' | 'captured';
  platform: 'teams';
  signal: 'dom-outline';
  initialHints: NormalizedHint[];
  hints: NormalizedHint[];
  unresolved: Array<{
    atMs: number;
    type: 'name-unresolved';
    platform: 'teams';
    signal: 'dom-outline';
    reason: 'resolver-empty';
    edge: 'start' | 'end';
  }>;
}

function installGlobal(
  key: string,
  value: unknown,
  restore: Array<() => void>,
): void {
  const target = globalThis as unknown as Record<string, unknown>;
  const hadOwn = Object.prototype.hasOwnProperty.call(target, key);
  const prior = target[key];
  target[key] = value;
  restore.push(() => {
    if (hadOwn) target[key] = prior;
    else delete target[key];
  });
}

function replay(
  trace: TeamsProducerDomTrace,
  displayNames: Record<TeamsProducerDomTraceNameToken, string> = DISPLAY_NAMES,
  rootAriaLabels: Readonly<Record<string, string>> = {},
): NormalizedReplay {
  const clock = new VirtualClock();
  const tiles = new Map<string, FakeElement>();
  for (const event of trace.events) {
    if (!tiles.has(event.tileId)) {
      tiles.set(event.tileId, new FakeElement('DIV', {
        'data-participant-id': event.tileId,
        'aria-label': rootAriaLabels[event.tileId] ?? '',
      }));
    }
  }

  let cursor = 0;
  while (cursor < trace.events.length && trace.events[cursor]!.atMs === 0) {
    const event = trace.events[cursor]!;
    tiles.get(event.tileId)!.apply(event, displayNames);
    cursor++;
  }

  const body = new FakeElement('BODY');
  const hints: NormalizedHint[] = [];
  const unresolved: NormalizedReplay['unresolved'] = [];
  const restore: Array<() => void> = [];
  const originalDateNow = Date.now;
  Date.now = () => BASE_EPOCH_MS + clock.atMs;
  restore.push(() => { Date.now = originalDateNow; });
  installGlobal('HTMLElement', FakeElement, restore);
  installGlobal('MutationObserver', FakeMutationObserver, restore);
  installGlobal('requestAnimationFrame', (callback: () => void) =>
    clock.requestAnimationFrame(callback), restore);
  installGlobal('cancelAnimationFrame', (id: number) => clock.clear(id), restore);
  installGlobal('setTimeout', (callback: () => void, delayMs?: number) =>
    clock.setTimeout(callback, delayMs), restore);
  installGlobal('clearTimeout', (id: number) => clock.clear(id), restore);
  installGlobal('setInterval', (callback: () => void, intervalMs?: number) =>
    clock.setInterval(callback, intervalMs), restore);
  installGlobal('clearInterval', (id: number) => clock.clear(id), restore);
  installGlobal('document', {
    body,
    querySelector: (selector: string) => selector === '[role="main"]' ? body : null,
    querySelectorAll: (selector: string) =>
      selector === '[data-tid*="participant"]' ? [...tiles.values()] : [],
  }, restore);

  const watcher = createTeamsSpeakers({
    debounceMs: 0,
    heartbeatMs: 400,
    onSpeaking: (name, tileId, isEnd, tMs) => hints.push({
      atMs: tMs - BASE_EPOCH_MS,
      tileId,
      name,
      edge: isEnd ? 'end' : 'start',
    }),
    onNameUnresolved: (observation: TeamsNameUnresolvedObservation) => {
      unresolved.push({
        atMs: observation.tMs - BASE_EPOCH_MS,
        type: observation.type,
        platform: observation.platform,
        signal: observation.signal,
        reason: observation.reason,
        edge: observation.edge,
      });
    },
  });

  try {
    clock.advanceTo(0);
    const initialHints = hints.map((hint) => ({ ...hint }));
    while (cursor < trace.events.length) {
      const atMs = trace.events[cursor]!.atMs;
      clock.advanceTo(atMs);
      while (cursor < trace.events.length && trace.events[cursor]!.atMs === atMs) {
        const event = trace.events[cursor]!;
        tiles.get(event.tileId)!.apply(event, displayNames);
        cursor++;
      }
    }
    clock.advanceTo(trace.events.at(-1)!.atMs + 150);
    return {
      provenance: trace.header.provenance,
      platform: trace.header.platform,
      signal: trace.header.signal,
      initialHints,
      hints,
      unresolved,
    };
  } finally {
    watcher.destroy();
    for (const undo of restore.reverse()) undo();
  }
}

function row(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    record: 'tile-state',
    atMs: 0,
    tileId: 'tile-001',
    nameToken: 'SPEAKER_A',
    signalState: 'present',
    voiceState: 'speaking',
    ...overrides,
  };
}

function jsonl(...records: Array<Record<string, unknown>>): string {
  const header = {
    record: 'header',
    schema: 'producer_dom_trace.v1',
    platform: 'teams',
    signal: 'dom-outline',
    provenance: 'authored',
    timebase: 'relative-ms',
  };
  return `${[header, ...records].map((record) => JSON.stringify(record)).join('\n')}\n`;
}

function expectReject(
  label: string,
  input: string,
  expectedCode?: TeamsProducerDomTraceAdmissionCode,
): void {
  try {
    parseTeamsProducerDomTrace(input);
  } catch (error) {
    if (!(error instanceof TeamsProducerDomTraceAdmissionError)) {
      throw new Error(`validator rejected ${label} without a typed admission error`);
    }
    if (expectedCode !== undefined && error.code !== expectedCode) {
      throw new Error(
        `validator rejected ${label} with ${error.code}; expected ${expectedCode}`,
      );
    }
    if (input.includes('must-never-cross') && error.message.includes('must-never-cross')) {
      throw new Error(`validator echoed rejected private input for ${label}`);
    }
    console.log(`  ✅ rejects ${label} code=${error.code}`);
    return;
  }
  throw new Error(`validator admitted ${label}`);
}

const trace = parseTeamsProducerDomTrace(FIXTURE_TEXT);
if (trace.header.provenance !== 'authored') {
  throw new Error('fixture provenance must state authored honestly');
}
if (serializeTeamsProducerDomTrace(trace) !== FIXTURE_TEXT) {
  throw new Error('fixture is not canonical producer_dom_trace.v1 JSONL');
}
console.log(`C797_TEAMS_TRACE_VALID records=${trace.events.length} provenance=${trace.header.provenance}`);
const capturedText = FIXTURE_TEXT.replace(
  '"provenance":"authored"',
  '"provenance":"captured"',
);
const capturedTrace = parseTeamsProducerDomTrace(capturedText);
if (
  capturedTrace.header.provenance !== 'captured'
  || serializeTeamsProducerDomTrace(capturedTrace) !== capturedText
) {
  throw new Error('captured provenance was rejected or relabeled during roundtrip');
}
try {
  serializeTeamsProducerDomTrace({ header: trace.header, events: [] });
  throw new Error('serializer admitted a header-only trace');
} catch (error) {
  if (!(error instanceof TeamsProducerDomTraceAdmissionError)) throw error;
}

expectReject('invalid JSON second record', `${jsonl().trimEnd()}\n{\n`, 'invalid-json');
expectReject('unknown keys', jsonl(row({ surprise: true })), 'unknown-field');
expectReject(
  'private unknown key without echo',
  jsonl(row({ 'must-never-cross': true })),
  'unknown-field',
);
for (const rawKey of [
  'participantId',
  'textContent',
  'innerHTML',
  'aria-label',
  'title',
  'URL',
]) {
  expectReject(
    `raw ${rawKey}`,
    jsonl(row({ [rawKey]: 'captured-value' })),
    'raw-field',
  );
}
expectReject(
  'raw class arrays',
  jsonl(row({ classes: ['vdi-frame-occlusion'] })),
  'raw-field',
);
expectReject('epoch atMs', jsonl(row({ atMs: BASE_EPOCH_MS })), 'time-not-relative');
expectReject('nonmonotonic atMs', jsonl(row({ atMs: 20 }), row({
  atMs: 19,
  tileId: 'tile-002',
})), 'time-nonmonotonic');
expectReject(
  'unbounded tile IDs',
  jsonl(row({ tileId: 'tile-this-id-is-too-long' })),
  'invalid-tile-id',
);
expectReject(
  'name-like bounded tile IDs',
  jsonl(row({ tileId: 'tile-alice' })),
  'invalid-tile-id',
);
expectReject(
  'arbitrary name tokens',
  jsonl(row({ nameToken: 'CUSTOMER_NAME' })),
  'invalid-enum',
);
expectReject(
  'noncanonical platform',
  jsonl(row()).replace('"platform":"teams"', '"platform":"zoom"'),
  'invalid-header',
);
expectReject(
  'noncanonical provenance',
  jsonl(row()).replace('"provenance":"authored"', '"provenance":"synthetic"'),
  'invalid-header',
);
expectReject(
  'duplicate name token carrying private input',
  jsonl(row()).replace(
    '"nameToken":"SPEAKER_A"',
    '"nameToken":"must-never-cross","nameToken":"SPEAKER_A"',
  ),
  'invalid-record',
);
expectReject(
  'duplicate provenance',
  jsonl(row()).replace(
    '"provenance":"authored"',
    '"provenance":"captured","provenance":"authored"',
  ),
  'invalid-record',
);
const atRecordLimit = parseTeamsProducerDomTrace(jsonl(...Array.from(
  { length: TEAMS_PRODUCER_DOM_TRACE_MAX_RECORDS - 1 },
  (_, index) => row({ atMs: index }),
)));
const atTileLimit = parseTeamsProducerDomTrace(jsonl(...Array.from(
  { length: TEAMS_PRODUCER_DOM_TRACE_MAX_TILES },
  (_, index) => row({
    atMs: index,
    tileId: `tile-${String(index + 1).padStart(3, '0')}`,
  }),
)));
if (
  atRecordLimit.events.length !== TEAMS_PRODUCER_DOM_TRACE_MAX_RECORDS - 1
  || new Set(atTileLimit.events.map((event) => event.tileId)).size
    !== TEAMS_PRODUCER_DOM_TRACE_MAX_TILES
) {
  throw new Error('literal producer_dom_trace.v1 bounds rejected their exact maxima');
}
console.log(
  `C797_TEAMS_TRACE_BOUNDS_GREEN records=${TEAMS_PRODUCER_DOM_TRACE_MAX_RECORDS} `
  + `tiles=${TEAMS_PRODUCER_DOM_TRACE_MAX_TILES}`,
);
expectReject(
  `more than ${TEAMS_PRODUCER_DOM_TRACE_MAX_RECORDS} records`,
  jsonl(...Array.from(
    { length: TEAMS_PRODUCER_DOM_TRACE_MAX_RECORDS },
    (_, index) => row({ atMs: index }),
  )),
  'record-limit',
);
expectReject(
  `more than ${TEAMS_PRODUCER_DOM_TRACE_MAX_TILES} unique tiles`,
  jsonl(...Array.from(
    { length: TEAMS_PRODUCER_DOM_TRACE_MAX_TILES + 1 },
    (_, index) => row({
      atMs: index,
      tileId: `tile-${String(index + 1).padStart(3, '0')}`,
    }),
  )),
  'tile-limit',
);

const first = replay(trace);
const second = replay(trace);
const firstBytes = JSON.stringify(first);
const secondBytes = JSON.stringify(second);
if (firstBytes !== secondBytes) {
  throw new Error(`replay is nondeterministic\nfirst=${firstBytes}\nsecond=${secondBytes}`);
}

const initialStarts = first.initialHints.filter((hint) => hint.edge === 'start');
if (
  initialStarts.length !== 2
  || initialStarts.some((hint) => hint.atMs !== 0)
  || initialStarts.map((hint) => hint.name).join('|') !== 'Anna Example|Michael Example'
) {
  throw new Error(`expected two simultaneous named starts without handover: ${firstBytes}`);
}
if (first.initialHints.some((hint) => hint.edge === 'end')) {
  throw new Error(`concurrent Teams starts forced a handover: ${firstBytes}`);
}
if (first.initialHints.some((hint) => hint.tileId === 'tile-003')) {
  throw new Error(`missing-name speaking tile crossed as a hint: ${firstBytes}`);
}
if (first.hints.some((hint) => hint.tileId === 'tile-004')) {
  throw new Error(`signal-absent tile crossed as a hint: ${firstBytes}`);
}
if (
  first.unresolved.length !== 1
  || first.unresolved[0]!.type !== 'name-unresolved'
  || first.unresolved[0]!.platform !== 'teams'
  || first.unresolved[0]!.signal !== 'dom-outline'
  || first.unresolved[0]!.reason !== 'resolver-empty'
  || first.unresolved[0]!.edge !== 'start'
  || first.unresolved[0]!.atMs !== 0
) {
  throw new Error(`missing-name tile did not emit exactly one unresolved start: ${firstBytes}`);
}
const lateNameHints = first.hints.filter((hint) => hint.tileId === 'tile-003');
if (
  lateNameHints.length !== 1
  || lateNameHints[0]!.name !== 'Boris Example'
  || lateNameHints[0]!.edge !== 'start'
  || lateNameHints[0]!.atMs !== 400
) {
  throw new Error(`late-painted known name did not recover on normal heartbeat: ${firstBytes}`);
}

const fallbackControlTrace = parseTeamsProducerDomTrace(jsonl(row({
  nameToken: 'UNRESOLVED',
})));
const fallbackControl = replay(
  fallbackControlTrace,
  DISPLAY_NAMES,
  { 'tile-001': 'name: mic' },
);
if (fallbackControl.hints.length !== 0) {
  throw new Error(
    `T6 aria-label fallback crossed an exact control as a name: `
    + JSON.stringify(fallbackControl.hints),
  );
}

const t6ControlSurfaces = [
  { token: 'CONTROL_MIC', value: 'mic' },
  { token: 'CONTROL_MIC_OFF', value: 'mic_off' },
  { token: 'CONTROL_MICROPHONE', value: 'Microphone' },
  { token: 'CONTROL_TIMER_MM_SS', value: '00:42' },
  { token: 'CONTROL_TIMER_HH_MM_SS', value: '01:02:03' },
] as const;
for (const control of t6ControlSurfaces) {
  const controlled = replay(trace, {
    ...DISPLAY_NAMES,
    SPEAKER_A: control.value,
  });
  const fabricated = controlled.hints.filter((hint) => hint.tileId === 'tile-001');
  if (fabricated.length !== 0) {
    throw new Error(
      `T6 ${control.token} crossed as a name: ${JSON.stringify(fabricated)}`,
    );
  }
}
console.log(
  `C797_TEAMS_T6_GREEN controls=${t6ControlSurfaces.length} `
  + 'selector_path=5 aria_fallback_path=1 fabricated_hints=0',
);

console.log(
  'C797_TEAMS_TRACE_GREEN '
  + 'concurrent_starts=2 unresolved_starts=1 absent_signal_hints=0 '
  + 'late_name_heartbeat=1 michael_resolved=1 replay_byte_identical=1',
);

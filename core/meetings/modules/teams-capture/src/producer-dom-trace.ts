/**
 * Sanitized, authored Teams DOM-observation tape.
 *
 * This is deliberately platform-local: a Teams voice outline is a
 * per-participant signal and multiple tiles may be speaking at the same time.
 * The tape records only canonical state enums, relative time, bounded fixture
 * tile IDs, and fixed pseudonym tokens. Captured provenance is admissible only
 * after the page-side sanitizer has reduced the DOM to this closed vocabulary.
 */

export const TEAMS_PRODUCER_DOM_TRACE_SCHEMA = 'producer_dom_trace.v1' as const;
/** Header included. Bounds validation memory before JSON parsing. */
export const TEAMS_PRODUCER_DOM_TRACE_MAX_RECORDS = 4096;
export const TEAMS_PRODUCER_DOM_TRACE_MAX_TILES = 64;

export const teamsProducerDomTraceNameTokens = [
  'SPEAKER_A',
  'SPEAKER_B',
  'SPEAKER_C',
  'UNRESOLVED',
] as const;

export type TeamsProducerDomTraceNameToken =
  typeof teamsProducerDomTraceNameTokens[number];

export interface TeamsProducerDomTraceHeader {
  record: 'header';
  schema: typeof TEAMS_PRODUCER_DOM_TRACE_SCHEMA;
  platform: 'teams';
  signal: 'dom-outline';
  provenance: 'authored' | 'captured';
  timebase: 'relative-ms';
}

export interface TeamsProducerDomTraceTileState {
  record: 'tile-state';
  atMs: number;
  tileId: string;
  nameToken: TeamsProducerDomTraceNameToken;
  signalState: 'present' | 'absent';
  voiceState: 'speaking' | 'silent';
}

export interface TeamsProducerDomTrace {
  header: TeamsProducerDomTraceHeader;
  events: TeamsProducerDomTraceTileState[];
}

export type TeamsProducerDomTraceAdmissionCode =
  | 'invalid-json'
  | 'invalid-record'
  | 'unknown-field'
  | 'raw-field'
  | 'invalid-header'
  | 'invalid-enum'
  | 'invalid-tile-id'
  | 'record-limit'
  | 'tile-limit'
  | 'time-not-relative'
  | 'time-nonmonotonic';

export class TeamsProducerDomTraceAdmissionError extends Error {
  constructor(
    readonly code: TeamsProducerDomTraceAdmissionCode,
    readonly line: number,
    message: string,
  ) {
    super(
      `TEAMS_PRODUCER_DOM_TRACE_${code.toUpperCase().replace(/-/g, '_')} `
      + `line=${line}: ${message}`,
    );
    this.name = 'TeamsProducerDomTraceAdmissionError';
  }
}

const HEADER_KEYS = [
  'record',
  'schema',
  'platform',
  'signal',
  'provenance',
  'timebase',
] as const;
const TILE_STATE_KEYS = [
  'record',
  'atMs',
  'tileId',
  'nameToken',
  'signalState',
  'voiceState',
] as const;
const NAME_TOKENS = new Set<string>(teamsProducerDomTraceNameTokens);
const FORBIDDEN_DOM_KEYS = new Set([
  'participantid',
  'textcontent',
  'innerhtml',
  'aria-label',
  'arialabel',
  'title',
  'url',
  'class',
  'classname',
  'classlist',
  'classes',
]);
// Host/browser pseudonymizers allocate ordinal IDs only. Accepting arbitrary
// bounded strings (for example, tile-alice) would still admit identity data.
const TILE_ID = /^tile-[0-9]{3}$/;
const MAX_RELATIVE_AT_MS = 10 * 60 * 1000;

function fail(
  code: TeamsProducerDomTraceAdmissionCode,
  line: number,
  message: string,
): never {
  throw new TeamsProducerDomTraceAdmissionError(code, line, message);
}

function asRecord(value: unknown, line: number): Record<string, unknown> {
  if (value === null || Array.isArray(value) || typeof value !== 'object') {
    fail('invalid-record', line, 'record must be a JSON object');
  }
  return value as Record<string, unknown>;
}

function assertKeys(
  value: Record<string, unknown>,
  allowed: readonly string[],
  line: number,
): void {
  const allowedSet = new Set(allowed);
  for (const key of Object.keys(value)) {
    const normalized = key.toLowerCase();
    if (FORBIDDEN_DOM_KEYS.has(normalized)) {
      fail('raw-field', line, 'raw DOM fields are forbidden');
    }
    if (!allowedSet.has(key)) {
      fail('unknown-field', line, 'record contains an unknown field');
    }
  }
  for (const key of allowed) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) {
      fail('invalid-record', line, `missing key "${key}"`);
    }
  }
}

function assertHeader(
  value: Record<string, unknown>,
  line: number,
): TeamsProducerDomTraceHeader {
  assertKeys(value, HEADER_KEYS, line);
  if (value.record !== 'header') {
    fail('invalid-header', line, 'first record must be "header"');
  }
  if (value.schema !== TEAMS_PRODUCER_DOM_TRACE_SCHEMA) {
    fail(
      'invalid-header',
      line,
      `schema must be "${TEAMS_PRODUCER_DOM_TRACE_SCHEMA}"`,
    );
  }
  if (value.platform !== 'teams') {
    fail('invalid-header', line, 'platform must be "teams"');
  }
  if (value.signal !== 'dom-outline') {
    fail('invalid-header', line, 'signal must be "dom-outline"');
  }
  if (value.provenance !== 'authored' && value.provenance !== 'captured') {
    fail('invalid-header', line, 'provenance must be "authored" or "captured"');
  }
  if (value.timebase !== 'relative-ms') {
    fail('invalid-header', line, 'timebase must be "relative-ms"');
  }
  return value as unknown as TeamsProducerDomTraceHeader;
}

function assertTileState(
  value: Record<string, unknown>,
  line: number,
): TeamsProducerDomTraceTileState {
  assertKeys(value, TILE_STATE_KEYS, line);
  if (value.record !== 'tile-state') {
    fail('invalid-record', line, 'record must be "tile-state"');
  }
  if (
    typeof value.atMs !== 'number'
    || !Number.isSafeInteger(value.atMs)
    || value.atMs < 0
    || value.atMs > MAX_RELATIVE_AT_MS
  ) {
    fail(
      'time-not-relative',
      line,
      `atMs must be a relative integer from 0 to ${MAX_RELATIVE_AT_MS}`,
    );
  }
  if (typeof value.tileId !== 'string' || !TILE_ID.test(value.tileId)) {
    fail('invalid-tile-id', line, 'tileId must be a three-digit ordinal');
  }
  if (typeof value.nameToken !== 'string' || !NAME_TOKENS.has(value.nameToken)) {
    fail('invalid-enum', line, 'nameToken must be a fixed pseudonym token');
  }
  if (value.signalState !== 'present' && value.signalState !== 'absent') {
    fail('invalid-enum', line, 'signalState must be "present" or "absent"');
  }
  if (value.voiceState !== 'speaking' && value.voiceState !== 'silent') {
    fail('invalid-enum', line, 'voiceState must be "speaking" or "silent"');
  }
  return value as unknown as TeamsProducerDomTraceTileState;
}

function canonicalHeader(header: TeamsProducerDomTraceHeader): string {
  return JSON.stringify({
    record: header.record,
    schema: header.schema,
    platform: header.platform,
    signal: header.signal,
    provenance: header.provenance,
    timebase: header.timebase,
  });
}

function canonicalTileState(event: TeamsProducerDomTraceTileState): string {
  return JSON.stringify({
    record: event.record,
    atMs: event.atMs,
    tileId: event.tileId,
    nameToken: event.nameToken,
    signalState: event.signalState,
    voiceState: event.voiceState,
  });
}

/** Parse and validate strict Teams producer_dom_trace.v1 JSONL. */
export function parseTeamsProducerDomTrace(jsonl: string): TeamsProducerDomTrace {
  if (jsonl.startsWith('\uFEFF')) {
    fail('invalid-record', 1, 'byte-order marks are not allowed');
  }
  const lines = jsonl.endsWith('\n')
    ? jsonl.slice(0, -1).split('\n')
    : jsonl.split('\n');
  if (lines.length < 2) {
    fail('invalid-record', 1, 'header and at least one tile-state are required');
  }
  if (lines.length > TEAMS_PRODUCER_DOM_TRACE_MAX_RECORDS) {
    fail(
      'record-limit',
      TEAMS_PRODUCER_DOM_TRACE_MAX_RECORDS + 1,
      `record count exceeds ${TEAMS_PRODUCER_DOM_TRACE_MAX_RECORDS}`,
    );
  }
  const blankLine = lines.findIndex((line) => line.trim() === '');
  if (blankLine >= 0) {
    fail('invalid-record', blankLine + 1, 'blank JSONL records are not allowed');
  }

  const records = lines.map((line, index) => {
    try {
      return asRecord(JSON.parse(line) as unknown, index + 1);
    } catch (error) {
      if (error instanceof TeamsProducerDomTraceAdmissionError) throw error;
      fail(
        'invalid-json',
        index + 1,
        'record is not valid JSON',
      );
    }
  });
  const header = assertHeader(records[0]!, 1);
  if (lines[0] !== canonicalHeader(header)) {
    fail(
      'invalid-record',
      1,
      'record is not canonical JSON; duplicate keys and alternate encodings are forbidden',
    );
  }
  const events: TeamsProducerDomTraceTileState[] = [];
  const tileIds = new Set<string>();
  let priorAtMs = -1;
  for (let index = 1; index < records.length; index++) {
    const event = assertTileState(records[index]!, index + 1);
    if (lines[index] !== canonicalTileState(event)) {
      fail(
        'invalid-record',
        index + 1,
        'record is not canonical JSON; duplicate keys and alternate encodings are forbidden',
      );
    }
    if (event.atMs < priorAtMs) {
      fail(
        'time-nonmonotonic',
        index + 1,
        `atMs ${event.atMs} is earlier than prior atMs ${priorAtMs}`,
      );
    }
    priorAtMs = event.atMs;
    if (!tileIds.has(event.tileId)) {
      if (tileIds.size >= TEAMS_PRODUCER_DOM_TRACE_MAX_TILES) {
        fail(
          'tile-limit',
          index + 1,
          `unique tile count exceeds ${TEAMS_PRODUCER_DOM_TRACE_MAX_TILES}`,
        );
      }
      const expectedTileId = `tile-${String(tileIds.size + 1).padStart(3, '0')}`;
      if (event.tileId !== expectedTileId) {
        fail(
          'invalid-tile-id',
          index + 1,
          `first-seen tileId must be the next ordinal ${expectedTileId}`,
        );
      }
      tileIds.add(event.tileId);
    }
    events.push(event);
  }
  return { header, events };
}

/** Canonical key order used for content comparison and review receipts. */
export function serializeTeamsProducerDomTrace(trace: TeamsProducerDomTrace): string {
  const lines = [
    canonicalHeader(trace.header),
    ...trace.events.map(canonicalTileState),
  ];
  const canonical = `${lines.join('\n')}\n`;
  // This is a public seam: type-shaped runtime input must not produce bytes the
  // corresponding admission parser would reject.
  parseTeamsProducerDomTrace(canonical);
  return canonical;
}

/**
 * Sanitized Zoom producer-DOM replay input.
 *
 * This is deliberately narrower than a DOM snapshot. A trace may state only
 * which of Zoom's canonical active-speaker views matched, whether its canonical
 * footer was absent/empty/named/faulting, and a fixed pseudonym when named.
 * Raw DOM, display text, classes, accessibility strings, titles, and URLs are
 * never admissible.
 */

export const ZOOM_TRACE_SCHEMA = 'producer_dom_trace.v1' as const;
export const ZOOM_TRACE_POLL_MS = 250 as const;
export const ZOOM_TRACE_CONFIRM_POLLS = 2 as const;
/** 41m40s at the production 250ms poll; longer captures require a new admitted segment. */
export const ZOOM_TRACE_MAX_ROWS = 10_000 as const;

export const ZOOM_TRACE_VIEWS = [
  'none',
  'speaker-active',
  'speaker-bar-active',
  'single-main-active',
] as const;

export const ZOOM_TRACE_FOOTERS = [
  'absent',
  'empty',
  'named',
  'read-fault',
] as const;

export const ZOOM_TRACE_PSEUDONYMS = [
  'speaker-a',
  'speaker-b',
  'speaker-c',
] as const;

export type ZoomTraceView = typeof ZOOM_TRACE_VIEWS[number];
export type ZoomTraceFooter = typeof ZOOM_TRACE_FOOTERS[number];
export type ZoomTracePseudonym = typeof ZOOM_TRACE_PSEUDONYMS[number];

export interface ZoomProducerDomTraceHeader {
  record: 'header';
  schema: typeof ZOOM_TRACE_SCHEMA;
  platform: 'zoom';
  signal: 'dom-active';
  provenance: 'authored' | 'captured';
  timebase: 'relative-ms';
  pollMs: typeof ZOOM_TRACE_POLL_MS;
  confirmPolls: typeof ZOOM_TRACE_CONFIRM_POLLS;
}

export type ZoomProducerDomTraceRow =
  | {
      atMs: number;
      view: Exclude<ZoomTraceView, 'none'>;
      footer: 'named';
      participant: ZoomTracePseudonym;
    }
  | {
      atMs: number;
      view: ZoomTraceView;
      footer: Exclude<ZoomTraceFooter, 'named'>;
    };

export interface ZoomProducerDomTrace {
  header: ZoomProducerDomTraceHeader;
  rows: ZoomProducerDomTraceRow[];
}

export type ZoomProducerDomTraceAdmissionCode =
  | 'invalid-json'
  | 'invalid-record'
  | 'unknown-field'
  | 'raw-field'
  | 'invalid-header'
  | 'invalid-enum'
  | 'invalid-pseudonym'
  | 'row-limit'
  | 'time-not-relative'
  | 'time-nonmonotonic'
  | 'time-off-cadence'
  | 'invalid-state';

export class ZoomProducerDomTraceAdmissionError extends Error {
  constructor(
    readonly code: ZoomProducerDomTraceAdmissionCode,
    readonly line: number,
    message: string,
  ) {
    super(`ZOOM_PRODUCER_DOM_TRACE_${code.toUpperCase().replace(/-/g, '_')} line=${line}: ${message}`);
    this.name = 'ZoomProducerDomTraceAdmissionError';
  }
}

const HEADER_KEYS = new Set([
  'record',
  'schema',
  'platform',
  'signal',
  'provenance',
  'timebase',
  'pollMs',
  'confirmPolls',
]);
const ROW_KEYS = new Set(['atMs', 'view', 'footer', 'participant']);
const RAW_FIELD_KEYS = new Set([
  'dom',
  'rawdom',
  'html',
  'innerhtml',
  'outerhtml',
  'text',
  'textcontent',
  'class',
  'classname',
  'aria',
  'arialabel',
  'title',
  'url',
  'meetingurl',
  'href',
]);
const VIEW_SET = new Set<string>(ZOOM_TRACE_VIEWS);
const FOOTER_SET = new Set<string>(ZOOM_TRACE_FOOTERS);
const PSEUDONYM_SET = new Set<string>(ZOOM_TRACE_PSEUDONYMS);

function admission(
  code: ZoomProducerDomTraceAdmissionCode,
  line: number,
  message: string,
): never {
  throw new ZoomProducerDomTraceAdmissionError(code, line, message);
}

function asRecord(value: unknown, line: number): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    admission('invalid-record', line, 'record must be a JSON object');
  }
  return value as Record<string, unknown>;
}

function validateKeys(
  record: Record<string, unknown>,
  allowed: ReadonlySet<string>,
  line: number,
): void {
  for (const key of Object.keys(record)) {
    const normalized = key.toLowerCase().replace(/[-_]/g, '');
    if (RAW_FIELD_KEYS.has(normalized)) {
      admission('raw-field', line, 'raw producer fields are forbidden');
    }
    if (!allowed.has(key)) {
      admission('unknown-field', line, 'record contains an unknown field');
    }
  }
}

function parseJson(lineText: string, line: number): unknown {
  try {
    return JSON.parse(lineText) as unknown;
  } catch {
    return admission('invalid-json', line, 'record is not valid JSON');
  }
}

function parseHeader(value: unknown): ZoomProducerDomTraceHeader {
  const line = 1;
  const record = asRecord(value, line);
  validateKeys(record, HEADER_KEYS, line);
  if (
    record.record !== 'header'
    || record.schema !== ZOOM_TRACE_SCHEMA
    || record.platform !== 'zoom'
    || record.signal !== 'dom-active'
    || (record.provenance !== 'authored' && record.provenance !== 'captured')
    || record.timebase !== 'relative-ms'
    || record.pollMs !== ZOOM_TRACE_POLL_MS
    || record.confirmPolls !== ZOOM_TRACE_CONFIRM_POLLS
    || Object.keys(record).length !== HEADER_KEYS.size
  ) {
    admission(
      'invalid-header',
      line,
      'header must declare the closed Zoom 250ms/two-poll contract exactly',
    );
  }
  return record as unknown as ZoomProducerDomTraceHeader;
}

function canonicalHeader(header: ZoomProducerDomTraceHeader): string {
  return JSON.stringify({
    record: header.record,
    schema: header.schema,
    platform: header.platform,
    signal: header.signal,
    provenance: header.provenance,
    timebase: header.timebase,
    pollMs: header.pollMs,
    confirmPolls: header.confirmPolls,
  });
}

function canonicalRow(row: ZoomProducerDomTraceRow): string {
  return row.footer === 'named'
    ? JSON.stringify({
        atMs: row.atMs,
        view: row.view,
        footer: row.footer,
        participant: row.participant,
      })
    : JSON.stringify({
        atMs: row.atMs,
        view: row.view,
        footer: row.footer,
      });
}

function parseRow(
  value: unknown,
  line: number,
  previousAtMs: number | null,
): ZoomProducerDomTraceRow {
  const record = asRecord(value, line);
  validateKeys(record, ROW_KEYS, line);

  const { atMs, view, footer, participant } = record;
  if (!Number.isSafeInteger(atMs) || (atMs as number) < 0) {
    admission('time-not-relative', line, 'atMs must be a non-negative relative integer');
  }
  if ((atMs as number) >= 1_000_000_000_000) {
    admission('time-not-relative', line, 'epoch timestamps are forbidden; atMs is relative');
  }
  if (previousAtMs === null && atMs !== 0) {
    admission('time-not-relative', line, 'the first observation must start at atMs=0');
  }
  if (previousAtMs !== null && (atMs as number) <= previousAtMs) {
    admission('time-nonmonotonic', line, 'atMs must increase strictly');
  }
  if (
    previousAtMs !== null
    && (atMs as number) - previousAtMs !== ZOOM_TRACE_POLL_MS
  ) {
    admission('time-off-cadence', line, 'each row must advance exactly one 250ms Zoom poll');
  }

  if (typeof view !== 'string' || !VIEW_SET.has(view)) {
    admission('invalid-enum', line, 'view is not a canonical Zoom trace view');
  }
  if (typeof footer !== 'string' || !FOOTER_SET.has(footer)) {
    admission('invalid-enum', line, 'footer is not a canonical Zoom trace footer state');
  }
  if (footer === 'named') {
    if (typeof participant !== 'string' || !PSEUDONYM_SET.has(participant)) {
      admission('invalid-pseudonym', line, 'named rows require a fixed speaker-a/b/c pseudonym');
    }
    if (view === 'none') {
      admission('invalid-state', line, 'view=none cannot carry a named footer');
    }
    if (Object.keys(record).length !== 4) {
      admission('invalid-record', line, 'named rows contain exactly atMs/view/footer/participant');
    }
  } else {
    if ('participant' in record) {
      admission('invalid-state', line, 'non-named rows cannot carry participant identity');
    }
    if (view === 'none' && footer !== 'absent') {
      admission('invalid-state', line, 'view=none requires footer=absent');
    }
    if (view !== 'none' && footer === 'absent') {
      // A matched active container with no footer is the footer-absent producer
      // observation. This is admissible and distinct from view=none.
    }
    if (Object.keys(record).length !== 3) {
      admission('invalid-record', line, 'unresolved rows contain exactly atMs/view/footer');
    }
  }

  return record as unknown as ZoomProducerDomTraceRow;
}

export function parseZoomProducerDomTrace(jsonl: string): ZoomProducerDomTrace {
  if (typeof jsonl !== 'string' || jsonl.length === 0) {
    admission('invalid-record', 1, 'trace is empty');
  }
  const lines = jsonl.split(/\r?\n/);
  if (lines[lines.length - 1] === '') lines.pop();
  if (lines.length < 2) {
    admission('invalid-record', 1, 'trace requires one header and at least one observation');
  }
  if (lines.length - 1 > ZOOM_TRACE_MAX_ROWS) {
    admission(
      'row-limit',
      ZOOM_TRACE_MAX_ROWS + 2,
      `trace exceeds the literal ${ZOOM_TRACE_MAX_ROWS}-row admission limit`,
    );
  }
  const blankIndex = lines.findIndex((line) => line.trim().length === 0);
  if (blankIndex >= 0) {
    admission('invalid-record', blankIndex + 1, 'blank records are forbidden');
  }

  const header = parseHeader(parseJson(lines[0], 1));
  if (lines[0] !== canonicalHeader(header)) {
    admission(
      'invalid-record',
      1,
      'record is not canonical JSON; duplicate keys and alternate encodings are forbidden',
    );
  }
  const rows: ZoomProducerDomTraceRow[] = [];
  let previousAtMs: number | null = null;
  for (let index = 1; index < lines.length; index++) {
    const row = parseRow(parseJson(lines[index], index + 1), index + 1, previousAtMs);
    if (lines[index] !== canonicalRow(row)) {
      admission(
        'invalid-record',
        index + 1,
        'record is not canonical JSON; duplicate keys and alternate encodings are forbidden',
      );
    }
    rows.push(row);
    previousAtMs = row.atMs;
  }
  return { header, rows };
}

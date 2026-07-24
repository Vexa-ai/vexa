/**
 * #956 C3 — deterministic post-producer speaker fixture matrix.
 *
 * Each scenario authors one PCM16 WAV and one truth/timeline. Platform views
 * reuse those exact acoustic bytes and differ only in their captured-signal
 * header and HintEvent stream. This module models the recordHint boundary; it
 * deliberately does not model or validate Teams/Zoom DOM extraction (#797).
 */
import { createHash } from 'node:crypto';

export const MATRIX_SAMPLE_RATE = 16_000;
export const MATRIX_FRAME_MS = 250;
export const MATRIX_STARTED_AT_MS = Date.parse('2024-06-10T06:13:20.000Z');
export const MATRIX_JITSI_DELAY_MS = 2_803;

const TURN_MS = 4_000;
const LEAD_IN_MS = 1_000;
const TAIL_OUT_MS = 2_000;
const SPEAKERS = ['Anna', 'Boris', 'Galina'] as const;

export type MatrixPlatform = 'teams' | 'jitsi' | 'zoom';
export type MatrixScenarioId =
  | 'direct'
  | 'silence'
  | 'overlap'
  | 'three-speaker'
  | 'self-resume';

export interface MatrixTurn {
  id: string;
  index: number;
  speaker: string;
  startMs: number;
  endMs: number;
}

export interface MatrixScenario {
  id: MatrixScenarioId;
  dials: {
    speakers: readonly string[];
    turns: number;
    separationMs: number;
  };
  turns: MatrixTurn[];
  durationMs: number;
  timelineBytes: Uint8Array;
  truthBytes: Uint8Array;
  wavBytes: Uint8Array;
  timelineSha256: string;
  truthSha256: string;
  wavSha256: string;
  acousticRecords: CapturedSignalRecord[];
}

export interface MatrixFixture {
  id: string;
  platform: MatrixPlatform;
  scenario: MatrixScenario;
  hints: HintRecord[];
  records: CapturedSignalRecord[];
  bytes: Uint8Array;
}

export interface SessionHeader {
  type: 'captured_signal_header';
  v: 1;
  platform: MatrixPlatform;
  native_meeting_id: string;
  language: 'en';
  lane: 'mixed';
  sample_rate: number;
  started_at: string;
}

export interface FrameRecord {
  seq: number;
  ts: number;
  speakerIndex: 999;
  pcm: string;
  pcm_len: number;
  rms: number;
  lane: 'mixed';
}

export interface HintRecord {
  type: 'hint';
  t: number;
  name: string;
  isEnd?: boolean;
}

export interface BoundaryRecord {
  type: 'boundary';
  tMs: number;
  kind:
    | 'silence→speaker'
    | 'speaker→silence'
    | 'speaker→speaker'
    | 'overlap-onset'
    | 'overlap-offset';
  confidence: number;
}

export type CapturedSignalRecord =
  | SessionHeader
  | FrameRecord
  | HintRecord
  | BoundaryRecord;

interface ScenarioSpec {
  id: MatrixScenarioId;
  speakers: readonly string[];
  turns: number;
  separationMs: number;
}

const SCENARIOS: readonly ScenarioSpec[] = [
  { id: 'direct', speakers: SPEAKERS.slice(0, 2), turns: 8, separationMs: 0 },
  { id: 'silence', speakers: SPEAKERS.slice(0, 2), turns: 8, separationMs: 2_000 },
  { id: 'overlap', speakers: SPEAKERS.slice(0, 2), turns: 8, separationMs: -600 },
  { id: 'three-speaker', speakers: SPEAKERS, turns: 8, separationMs: 0 },
  { id: 'self-resume', speakers: SPEAKERS.slice(0, 1), turns: 2, separationMs: 2_000 },
];

const sha256 = (bytes: Uint8Array): string =>
  createHash('sha256').update(bytes).digest('hex');

const jsonBytes = (value: unknown): Uint8Array =>
  Buffer.from(`${JSON.stringify(value)}\n`, 'utf8');

function encodeWav(samples: Int16Array): Uint8Array {
  const dataBytes = samples.byteLength;
  const wav = Buffer.alloc(44 + dataBytes);
  wav.write('RIFF', 0, 'ascii');
  wav.writeUInt32LE(36 + dataBytes, 4);
  wav.write('WAVE', 8, 'ascii');
  wav.write('fmt ', 12, 'ascii');
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(MATRIX_SAMPLE_RATE, 24);
  wav.writeUInt32LE(MATRIX_SAMPLE_RATE * 2, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write('data', 36, 'ascii');
  wav.writeUInt32LE(dataBytes, 40);
  for (let index = 0; index < samples.length; index++) {
    wav.writeInt16LE(samples[index], 44 + index * 2);
  }
  return wav;
}

function authoredSamples(turns: readonly MatrixTurn[], durationMs: number): Int16Array {
  const sampleCount = Math.ceil((durationMs * MATRIX_SAMPLE_RATE) / 1000);
  const samples = new Int16Array(sampleCount);
  for (const turn of turns) {
    const speakerIndex = SPEAKERS.indexOf(turn.speaker as (typeof SPEAKERS)[number]);
    const start = Math.round((turn.startMs * MATRIX_SAMPLE_RATE) / 1000);
    const end = Math.min(
      sampleCount,
      Math.round((turn.endMs * MATRIX_SAMPLE_RATE) / 1000),
    );
    const period = 37 + speakerIndex * 16;
    for (let sample = start; sample < end; sample++) {
      const phase = (sample - start + turn.index * 11) % period;
      const contribution = phase < period / 2 ? 2_800 : -2_800;
      samples[sample] = Math.max(
        -32_768,
        Math.min(32_767, samples[sample] + contribution),
      );
    }
  }
  return samples;
}

function rms(samples: Int16Array, start: number, end: number): number {
  let sum = 0;
  for (let index = start; index < end; index++) {
    const value = samples[index] / 32_768;
    sum += value * value;
  }
  return Number(Math.sqrt(sum / Math.max(1, end - start)).toFixed(8));
}

function encodeFloat32(samples: Int16Array, start: number, end: number): string {
  const bytes = Buffer.alloc((end - start) * 4);
  for (let index = start; index < end; index++) {
    bytes.writeFloatLE(samples[index] / 32_768, (index - start) * 4);
  }
  return bytes.toString('base64');
}

function boundariesFor(turns: readonly MatrixTurn[]): BoundaryRecord[] {
  const boundaries: BoundaryRecord[] = [];
  for (let index = 0; index < turns.length; index++) {
    const turn = turns[index];
    const previous = turns[index - 1];
    if (!previous) {
      boundaries.push({
        type: 'boundary',
        tMs: MATRIX_STARTED_AT_MS + turn.startMs,
        kind: 'silence→speaker',
        confidence: 1,
      });
    } else if (turn.startMs < previous.endMs) {
      boundaries.push({
        type: 'boundary',
        tMs: MATRIX_STARTED_AT_MS + turn.startMs,
        kind: 'overlap-onset',
        confidence: 1,
      });
      boundaries.push({
        type: 'boundary',
        tMs: MATRIX_STARTED_AT_MS + previous.endMs,
        kind: 'overlap-offset',
        confidence: 1,
      });
    } else if (turn.startMs === previous.endMs) {
      boundaries.push({
        type: 'boundary',
        tMs: MATRIX_STARTED_AT_MS + turn.startMs,
        kind: 'speaker→speaker',
        confidence: 1,
      });
    } else {
      boundaries.push({
        type: 'boundary',
        tMs: MATRIX_STARTED_AT_MS + previous.endMs,
        kind: 'speaker→silence',
        confidence: 1,
      });
      boundaries.push({
        type: 'boundary',
        tMs: MATRIX_STARTED_AT_MS + turn.startMs,
        kind: 'silence→speaker',
        confidence: 1,
      });
    }
  }
  const last = turns[turns.length - 1];
  boundaries.push({
    type: 'boundary',
    tMs: MATRIX_STARTED_AT_MS + last.endMs,
    kind: 'speaker→silence',
    confidence: 1,
  });
  return boundaries;
}

function framesFor(samples: Int16Array, durationMs: number): FrameRecord[] {
  const frameSamples = (MATRIX_SAMPLE_RATE * MATRIX_FRAME_MS) / 1000;
  const frames: FrameRecord[] = [];
  const frameCount = Math.ceil(durationMs / MATRIX_FRAME_MS);
  for (let seq = 0; seq < frameCount; seq++) {
    const start = seq * frameSamples;
    const end = Math.min(samples.length, start + frameSamples);
    frames.push({
      seq,
      ts: MATRIX_STARTED_AT_MS + seq * MATRIX_FRAME_MS,
      speakerIndex: 999,
      pcm: encodeFloat32(samples, start, end),
      pcm_len: end - start,
      rms: rms(samples, start, end),
      lane: 'mixed',
    });
  }
  return frames;
}

function buildScenario(spec: ScenarioSpec): MatrixScenario {
  const turns: MatrixTurn[] = [];
  let cursor = LEAD_IN_MS;
  for (let index = 0; index < spec.turns; index++) {
    const speaker = spec.speakers[index % spec.speakers.length];
    turns.push({
      id: `seg_${index}`,
      index,
      speaker,
      startMs: cursor,
      endMs: cursor + TURN_MS,
    });
    cursor += TURN_MS + spec.separationMs;
  }
  const durationMs = turns[turns.length - 1].endMs + TAIL_OUT_MS;
  const samples = authoredSamples(turns, durationMs);
  const wavBytes = encodeWav(samples);
  const truthBytes = Buffer.from(
    turns.map((turn) => JSON.stringify(turn)).join('\n') + '\n',
    'utf8',
  );
  const timelineBytes = jsonBytes({
    type: 'causal-speaker-authored-timeline',
    v: 1,
    scenario: spec.id,
    sampleRate: MATRIX_SAMPLE_RATE,
    frameMs: MATRIX_FRAME_MS,
    dials: {
      speakers: spec.speakers,
      turns: spec.turns,
      turnMs: TURN_MS,
      separationMs: spec.separationMs,
      leadInMs: LEAD_IN_MS,
      tailOutMs: TAIL_OUT_MS,
    },
    turns,
  });
  return {
    id: spec.id,
    dials: {
      speakers: spec.speakers,
      turns: spec.turns,
      separationMs: spec.separationMs,
    },
    turns,
    durationMs,
    timelineBytes,
    truthBytes,
    wavBytes,
    timelineSha256: sha256(timelineBytes),
    truthSha256: sha256(truthBytes),
    wavSha256: sha256(wavBytes),
    acousticRecords: [
      ...framesFor(samples, durationMs),
      ...boundariesFor(turns),
    ],
  };
}

function teamsHints(scenario: MatrixScenario): HintRecord[] {
  const hints: HintRecord[] = [];
  for (const turn of scenario.turns) {
    hints.push({
      type: 'hint',
      t: MATRIX_STARTED_AT_MS + turn.startMs + 500,
      name: turn.speaker,
    });
    hints.push({
      type: 'hint',
      t: MATRIX_STARTED_AT_MS + turn.startMs + 2_500,
      name: turn.speaker,
    });
    hints.push({
      type: 'hint',
      t: MATRIX_STARTED_AT_MS + turn.endMs + 300,
      name: turn.speaker,
      isEnd: true,
    });
  }
  return hints;
}

function jitsiHints(scenario: MatrixScenario): HintRecord[] {
  const first = scenario.turns[0];
  const hints: HintRecord[] = [{
    type: 'hint',
    // The first observation establishes a baseline after the first acoustic
    // onset. It cannot name that turn, and it keeps the following transition's
    // signal epoch from also containing the baseline turn.
    t: MATRIX_STARTED_AT_MS + first.startMs + 200,
    name: first.speaker,
  }];
  let current = first.speaker;
  for (const turn of scenario.turns.slice(1)) {
    hints.push({
      type: 'hint',
      t: MATRIX_STARTED_AT_MS + turn.startMs + 200,
      name: current,
    });
    if (turn.speaker === current) continue;
    const transitionAt = MATRIX_STARTED_AT_MS + turn.startMs + MATRIX_JITSI_DELAY_MS;
    hints.push({
      type: 'hint',
      t: transitionAt,
      name: current,
      isEnd: true,
    });
    hints.push({
      type: 'hint',
      t: transitionAt,
      name: turn.speaker,
    });
    current = turn.speaker;
  }
  const last = scenario.turns[scenario.turns.length - 1];
  hints.push({
    type: 'hint',
    t: MATRIX_STARTED_AT_MS + last.endMs + 1_000,
    name: current,
    isEnd: true,
  });
  return hints;
}

function zoomHints(scenario: MatrixScenario): HintRecord[] {
  if (scenario.id !== 'direct') {
    throw new Error(
      `zoom fixture scope is direct non-regression only; ${scenario.id} remains #797`,
    );
  }
  const hints: HintRecord[] = [];
  let current: string | null = null;
  for (const turn of scenario.turns) {
    const observedAt = MATRIX_STARTED_AT_MS + turn.startMs + 500;
    if (current !== null) {
      hints.push({ type: 'hint', t: observedAt, name: current, isEnd: true });
    }
    hints.push({ type: 'hint', t: observedAt, name: turn.speaker });
    hints.push({
      type: 'hint',
      t: MATRIX_STARTED_AT_MS + turn.startMs + 2_500,
      name: turn.speaker,
    });
    current = turn.speaker;
  }
  const last = scenario.turns[scenario.turns.length - 1];
  hints.push({
    type: 'hint',
    t: MATRIX_STARTED_AT_MS + last.endMs + 500,
    name: current!,
    isEnd: true,
  });
  return hints;
}

const eventTime = (record: CapturedSignalRecord): number => {
  if ('ts' in record) return record.ts;
  if (record.type === 'hint') return record.t;
  if (record.type === 'boundary') return record.tMs;
  return -Infinity;
};

const eventOrder = (record: CapturedSignalRecord): number => {
  if ('type' in record && record.type === 'boundary') return 0;
  if ('type' in record && record.type === 'hint') return record.isEnd ? 1 : 2;
  return 3;
};

export function renderMatrixFixture(
  platform: MatrixPlatform,
  scenario: MatrixScenario,
  hintsOverride?: readonly HintRecord[],
): MatrixFixture {
  const hints = [...(hintsOverride ?? (
    platform === 'teams'
      ? teamsHints(scenario)
      : platform === 'jitsi'
        ? jitsiHints(scenario)
        : zoomHints(scenario)
  ))];
  const header: SessionHeader = {
    type: 'captured_signal_header',
    v: 1,
    platform,
    native_meeting_id: `c3-${scenario.id}-${platform}`,
    language: 'en',
    lane: 'mixed',
    sample_rate: MATRIX_SAMPLE_RATE,
    started_at: new Date(MATRIX_STARTED_AT_MS).toISOString(),
  };
  const events = [...scenario.acousticRecords, ...hints]
    .sort((left, right) =>
      eventTime(left) - eventTime(right) || eventOrder(left) - eventOrder(right));
  const records: CapturedSignalRecord[] = [header, ...events];
  return {
    id: `${scenario.id}/${platform}`,
    platform,
    scenario,
    hints,
    records,
    bytes: Buffer.from(
      records.map((record) => JSON.stringify(record)).join('\n') + '\n',
      'utf8',
    ),
  };
}

export function buildCausalSpeakerMatrix(): {
  scenarios: MatrixScenario[];
  fixtures: MatrixFixture[];
} {
  const scenarios = SCENARIOS.map(buildScenario);
  const fixtures = scenarios.flatMap((scenario) => [
    renderMatrixFixture('teams', scenario),
    renderMatrixFixture('jitsi', scenario),
    ...(scenario.id === 'direct'
      ? [renderMatrixFixture('zoom', scenario)]
      : []),
  ]);
  return { scenarios, fixtures };
}

export function acousticEvidenceBytes(fixture: MatrixFixture): Uint8Array {
  return Buffer.from(
    fixture.records
      .filter((record) =>
        !('type' in record)
        || (record.type !== 'captured_signal_header' && record.type !== 'hint'))
      .map((record) => JSON.stringify(record))
      .join('\n') + '\n',
    'utf8',
  );
}

export function capturedSignalBytes(
  fixture: MatrixFixture,
  records: readonly CapturedSignalRecord[],
): Uint8Array {
  return Buffer.from(
    records.map((record) => JSON.stringify(record)).join('\n') + '\n',
    'utf8',
  );
}

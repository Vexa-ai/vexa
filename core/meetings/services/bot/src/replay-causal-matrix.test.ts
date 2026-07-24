/**
 * #956 C3 — admitted causal speaker replay matrix.
 *
 * One deterministic authored WAV/truth/timeline is rendered into Teams and
 * Jitsi post-producer captured-signal views. Zoom gets a direct-handover
 * non-regression row only. Every tape crosses the delivered C1 custody port;
 * worker-local bytes are deleted before an independent reader may replay it.
 *
 * This is an offline attribution/custody oracle. Mock-free binder replay says
 * nothing about DOM extraction, ASR content, field latency, live traffic, or
 * deployment.
 */
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  ChunkedTranscriber,
  ClusterNameBinder,
  type BoundaryEvent,
  type BoundarySource,
  type ChunkSegment,
  type HintKind,
} from '@vexa/mixed-pipeline';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';
import {
  acousticEvidenceBytes,
  buildCausalSpeakerMatrix,
  capturedSignalBytes,
  MATRIX_JITSI_DELAY_MS,
  MATRIX_STARTED_AT_MS,
  renderMatrixFixture,
  type CapturedSignalRecord,
  type HintRecord,
  type MatrixFixture,
  type MatrixPlatform,
} from '../../../eval/src/causal-speaker-matrix.js';
import {
  SignalCustodyError,
  directorySignalCustody,
  type CaptureSignalCustodyReceipt,
} from './telemetry-custody.js';
import { hintKindForPlatform } from './pipeline.js';

const here = dirname(fileURLToPath(import.meta.url));
const manifestPath = join(
  here,
  '..',
  '..',
  '..',
  'eval',
  'replay-fixture',
  'causal-speaker-matrix',
  'manifest.json',
);

interface ManifestEntry {
  id: string;
  platform: MatrixPlatform;
  scenario: string;
  source: {
    wavSha256: string;
    truthSha256: string;
    timelineSha256: string;
  };
  receipt: CaptureSignalCustodyReceipt;
}

interface MatrixManifest {
  type: 'causal-speaker-replay-matrix';
  v: 1;
  base: string;
  digestDomain: 'uncompressed-captured-signal-jsonl';
  fixtures: ManifestEntry[];
}

interface TurnResult {
  id: string;
  truth: string;
  resolved: string;
  source: string;
  confidence: number;
}

interface ReplayScore {
  correct: number;
  unknown: number;
  invented: number;
  turns: number;
  authoredHintOnsetDelayMs: number[];
  authoredHintEndDelayMs: number[];
  authoredHintStaleOutgoingMs: number[];
  results: TurnResult[];
}

interface PipelineReplay {
  rows: Array<{
    id: string;
    speaker: string;
    text: string;
    startMs: number;
    endMs: number;
  }>;
  renames: Array<{
    from: string;
    to: string;
    ids: string[];
  }>;
}

const fail = (kind: string, message: string): never => {
  throw new Error(`C3_MATRIX_RED kind=${kind} ${message}`);
};

function readManifest(): MatrixManifest {
  try {
    return JSON.parse(readFileSync(manifestPath, 'utf8')) as MatrixManifest;
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === 'ENOENT') {
      fail('missing-manifest', `path=${manifestPath} replay_admitted=false`);
    }
    fail('invalid-manifest', `path=${manifestPath} error=${String(error)}`);
  }
}

function parseAdmittedFixture(
  expected: MatrixFixture,
  bytes: Uint8Array,
): MatrixFixture {
  const text = Buffer.from(bytes).toString('utf8');
  assert(text.endsWith('\n'), `${expected.id}: admitted JSONL must be newline-complete`);
  const records = text
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line) as CapturedSignalRecord);
  const header = records[0];
  assert(
    'type' in header && header.type === 'captured_signal_header',
    `${expected.id}: admitted tape must begin with a session header`,
  );
  assert.equal(header.platform, expected.platform, `${expected.id}: platform header drift`);
  assert.equal(
    header.native_meeting_id,
    `c3-${expected.scenario.id}-${expected.platform}`,
    `${expected.id}: native meeting id drift`,
  );
  const hints = records.filter(
    (record): record is HintRecord =>
      'type' in record && record.type === 'hint',
  );
  assert(hints.length > 0, `${expected.id}: admitted tape has no producer testimony`);
  assert(
    records.some((record) => !('type' in record)),
    `${expected.id}: admitted tape has no audio frames`,
  );
  assert(
    records.some((record) => 'type' in record && record.type === 'boundary'),
    `${expected.id}: admitted tape has no acoustic boundaries`,
  );
  return { ...expected, hints, records, bytes };
}

function asAbsolute(turnMs: number): number {
  return MATRIX_STARTED_AT_MS + turnMs;
}

function percentile(values: readonly number[], quantile: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.floor((sorted.length - 1) * quantile)];
}

function transitionStarts(hints: readonly HintRecord[]): Set<HintRecord> {
  const starts = new Set<HintRecord>();
  for (let index = 0; index < hints.length; index++) {
    const hint = hints[index];
    if (hint.isEnd) continue;
    const ended = hints.find((candidate) =>
      candidate.isEnd
      && candidate.t === hint.t
      && candidate.name !== hint.name);
    if (ended) starts.add(hint);
  }
  return starts;
}

function authoredHintMetrics(fixture: MatrixFixture): Pick<
  ReplayScore,
  | 'authoredHintOnsetDelayMs'
  | 'authoredHintEndDelayMs'
  | 'authoredHintStaleOutgoingMs'
> {
  const starts = fixture.platform === 'jitsi'
    ? transitionStarts(fixture.hints)
    : new Set(fixture.hints.filter((hint) => !hint.isEnd));
  const authoredHintOnsetDelayMs: number[] = [];
  const authoredHintEndDelayMs: number[] = [];
  const authoredHintStaleOutgoingMs: number[] = [];

  for (let index = 0; index < fixture.scenario.turns.length; index++) {
    const turn = fixture.scenario.turns[index];
    const onset = asAbsolute(turn.startMs);
    const end = asAbsolute(turn.endMs);
    const nextOnset = fixture.scenario.turns[index + 1]
      ? asAbsolute(fixture.scenario.turns[index + 1].startMs)
      : Infinity;
    const start = fixture.hints
      .filter((hint) =>
        starts.has(hint)
        && hint.name === turn.speaker
        && hint.t >= onset
        && hint.t < nextOnset + MATRIX_JITSI_DELAY_MS + 1)
      .sort((left, right) => left.t - right.t)[0];
    const identityEnd = fixture.hints
      .filter((hint) =>
        hint.isEnd
        && hint.name === turn.speaker
        && hint.t >= end - 1_000)
      .sort((left, right) => left.t - right.t)[0];
    if (start) authoredHintOnsetDelayMs.push(start.t - onset);
    if (identityEnd) authoredHintEndDelayMs.push(identityEnd.t - end);
    const previous = fixture.scenario.turns[index - 1];
    if (
      previous
      && previous.speaker !== turn.speaker
      && fixture.platform !== 'teams'
      && start
    ) {
      authoredHintStaleOutgoingMs.push(Math.max(0, start.t - onset));
    }
  }
  return {
    authoredHintOnsetDelayMs,
    authoredHintEndDelayMs,
    authoredHintStaleOutgoingMs,
  };
}

function acousticSealThrough(fixture: MatrixFixture): number {
  const header = fixture.records[0];
  assert('type' in header && header.type === 'captured_signal_header');
  const timestamps = fixture.records.flatMap((record): number[] => {
    if (!('type' in record)) {
      return [record.ts + (record.pcm_len / header.sample_rate) * 1000];
    }
    return record.type === 'boundary' ? [record.tMs] : [];
  });
  assert(timestamps.length > 0, `${fixture.id}: no admitted acoustic watermark`);
  return Math.max(...timestamps);
}

function replayBinder(
  fixture: MatrixFixture,
  hints: readonly HintRecord[] = fixture.hints,
  platform: MatrixPlatform = fixture.platform,
): ReplayScore {
  const binder = new ClusterNameBinder({});
  const kind = hintKindForPlatform(platform);
  for (const turn of fixture.scenario.turns) {
    binder.registerAcousticTurn({
      clusterId: turn.id,
      tStartMs: asAbsolute(turn.startMs),
      tEndMs: asAbsolute(turn.endMs),
    }, true);
  }
  for (const hint of [...hints].sort((left, right) =>
    left.t - right.t || Number(right.isEnd ?? false) - Number(left.isEnd ?? false))) {
    binder.recordHint({
      name: hint.name,
      tMs: hint.t,
      kind,
      isEnd: hint.isEnd,
    });
  }
  binder.sealAcousticThrough(acousticSealThrough(fixture) + 1);

  const results = fixture.scenario.turns.map((turn): TurnResult => {
    const resolved = binder.resolve({
      clusterId: turn.id,
      tStartMs: asAbsolute(turn.startMs),
      tEndMs: asAbsolute(turn.endMs),
    }, { recordVote: false, finalized: true });
    return {
      id: turn.id,
      truth: turn.speaker,
      resolved: resolved.speakerName,
      source: resolved.source,
      confidence: resolved.confidence,
    };
  });
  const correct = results.filter((result) => result.resolved === result.truth).length;
  const unknown = results.filter((result) => result.resolved === result.id).length;
  const invented = results.length - correct - unknown;
  return {
    correct,
    unknown,
    invented,
    turns: results.length,
    ...authoredHintMetrics(fixture),
    results,
  };
}

function framePcm(record: Extract<CapturedSignalRecord, { seq: number }>): Float32Array {
  const bytes = Buffer.from(record.pcm, 'base64');
  return new Float32Array(
    bytes.buffer,
    bytes.byteOffset,
    bytes.byteLength / Float32Array.BYTES_PER_ELEMENT,
  );
}

function authoredTranscription(pcm: Float32Array, call: number): TranscriptionResult {
  const duration = pcm.length / 16_000;
  const text = `matrix${call} orbit${call} cedar${call}`;
  return {
    text,
    language: 'en',
    language_probability: 0.99,
    duration,
    segments: [{
      start: 0,
      end: duration,
      text,
      no_speech_prob: 0,
      avg_logprob: -0.1,
      compression_ratio: 1,
    }],
  } as TranscriptionResult;
}

async function replayPipeline(
  fixture: MatrixFixture,
  admittedBytes: Uint8Array,
): Promise<PipelineReplay> {
  const rows = new Map<string, PipelineReplay['rows'][number]>();
  const renames: PipelineReplay['renames'] = [];
  let sttCall = 0;
  let emitBoundary!: (event: BoundaryEvent) => void;
  const write = (speaker: string, segments: readonly ChunkSegment[]): void => {
    for (const segment of segments) {
      rows.set(segment.segmentId, {
        id: segment.segmentId,
        speaker,
        text: segment.text,
        startMs: segment.startMs,
        endMs: segment.endMs,
      });
    }
  };
  const transcriber = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async (pcm) => authoredTranscription(pcm, sttCall++),
    publish: (speaker, confirmed, pending) => {
      write(speaker, confirmed);
      write(speaker, pending);
    },
    publishPending: (speaker, pending) => write(speaker, pending),
    clearPending: () => {},
    rename: (from, to, segments) => {
      renames.push({ from, to, ids: segments.map((segment) => segment.segmentId) });
      write(to, segments);
    },
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
      emitBoundary = onBoundary;
      return { appendFrame: async () => {}, reset() {} };
    },
    log: (message) => {
      if (process.env.C3_PIPELINE_DIAG === '1') {
        console.error(`[${fixture.id}] ${message}`);
      }
    },
  });

  const records = Buffer.from(admittedBytes)
    .toString('utf8')
    .trim()
    .split('\n')
    .slice(1)
    .map((line) => JSON.parse(line) as CapturedSignalRecord);
  for (const record of records) {
    if ('type' in record && record.type === 'hint') {
      transcriber.recordHint(
        record.name,
        hintKindForPlatform(fixture.platform),
        record.t,
        record.isEnd,
      );
    } else if ('type' in record && record.type === 'boundary') {
      emitBoundary({
        kind: record.kind,
        tMs: record.tMs,
        confidence: record.confidence,
      });
    } else if (!('type' in record)) {
      transcriber.feedAudio(framePcm(record), record.ts);
      await new Promise<void>((resolve) => setImmediate(resolve));
    }
  }
  await new Promise((resolve) => setTimeout(resolve, 2_000));
  await transcriber.dispose();
  return {
    rows: [...rows.values()]
      .filter((row) => row.text)
      .sort((left, right) =>
        left.startMs - right.startMs || left.id.localeCompare(right.id)),
    renames,
  };
}

function pipelineIdentityScore(
  fixture: MatrixFixture,
  replay: PipelineReplay,
): { correct: number; unknown: number; invented: number; unmatched: number } {
  let correct = 0;
  let unknown = 0;
  let invented = 0;
  let unmatched = 0;
  for (const row of replay.rows) {
    let truth: MatrixFixture['scenario']['turns'][number] | undefined;
    let overlap = 0;
    for (const turn of fixture.scenario.turns) {
      const amount = Math.min(row.endMs, asAbsolute(turn.endMs))
        - Math.max(row.startMs, asAbsolute(turn.startMs));
      if (amount > overlap) {
        overlap = amount;
        truth = turn;
      }
    }
    if (!truth) {
      unmatched++;
      continue;
    }
    if (row.speaker === truth.speaker) correct++;
    else if (/^seg_\d+$/.test(row.speaker)) unknown++;
    else invented++;
  }
  return { correct, unknown, invented, unmatched };
}

function assertTyped(
  expected: string,
  run: () => Promise<unknown>,
): Promise<void> {
  return run().then(
    () => fail('negative-control-green', `expected=${expected}`),
    (error: unknown) => {
      assert(error instanceof SignalCustodyError, `expected SignalCustodyError, got ${String(error)}`);
      assert.equal(error.kind, expected);
    },
  );
}

function manifestEntry(
  fixture: MatrixFixture,
  receipt: CaptureSignalCustodyReceipt,
): ManifestEntry {
  return {
    id: fixture.id,
    platform: fixture.platform,
    scenario: fixture.scenario.id,
    source: {
      wavSha256: fixture.scenario.wavSha256,
      truthSha256: fixture.scenario.truthSha256,
      timelineSha256: fixture.scenario.timelineSha256,
    },
    receipt,
  };
}

async function admitAll(
  fixtures: readonly MatrixFixture[],
  scratch: string,
): Promise<{ entries: ManifestEntry[]; readback: Map<string, Uint8Array> }> {
  const custodyRoot = join(scratch, 'off-worker-custody');
  const custody = directorySignalCustody(custodyRoot);
  const entries: ManifestEntry[] = [];
  const readback = new Map<string, Uint8Array>();

  for (const fixture of fixtures) {
    const worker = join(scratch, 'workers', fixture.id.replace('/', '-'));
    const sourcePath = join(worker, 'session.captured-signal.jsonl');
    mkdirSync(worker, { recursive: true });
    writeFileSync(sourcePath, fixture.bytes);
    const receipt = await custody.admit({
      sourcePath,
      expectedRecords: fixture.records.length,
    });
    const repeated = await custody.admit({
      sourcePath,
      expectedRecords: fixture.records.length,
    });
    assert.deepEqual(repeated, receipt, `${fixture.id}: admission must be idempotent`);

    rmSync(worker, { recursive: true, force: true });
    const freshReader = directorySignalCustody(custodyRoot);
    const persisted = await freshReader.load(receipt.digest);
    assert.deepEqual(persisted, receipt, `${fixture.id}: receipt survives worker deletion`);
    const independentlyRead = await freshReader.read(persisted);
    assert.deepEqual(
      Buffer.from(independentlyRead),
      Buffer.from(fixture.bytes),
      `${fixture.id}: independent readback preserves exact tape bytes`,
    );
    entries.push(manifestEntry(fixture, receipt));
    readback.set(fixture.id, independentlyRead);
  }
  return { entries, readback };
}

async function main(): Promise<void> {
  const expectedManifest = readManifest();
  const scratch = mkdtempSync(join(tmpdir(), 'vexa-c3-speaker-matrix-'));
  try {
    const first = buildCausalSpeakerMatrix();
    const second = buildCausalSpeakerMatrix();
    assert.deepEqual(
      first.fixtures.map((fixture) => Buffer.from(fixture.bytes)),
      second.fixtures.map((fixture) => Buffer.from(fixture.bytes)),
      'same authored input must render byte-identical platform tapes',
    );

    for (const scenario of first.scenarios) {
      const views = first.fixtures.filter((fixture) => fixture.scenario.id === scenario.id);
      const reference = acousticEvidenceBytes(views[0]);
      for (const view of views.slice(1)) {
        assert.deepEqual(
          Buffer.from(acousticEvidenceBytes(view)),
          Buffer.from(reference),
          `${scenario.id}: platform views must share exact frames and boundaries`,
        );
      }
    }

    const admitted = await admitAll(first.fixtures, scratch);
    const actualManifest: MatrixManifest = {
      type: 'causal-speaker-replay-matrix',
      v: 1,
      base: 'a56b9a2651f5112e3c5b74442c614e6af84de3b3',
      digestDomain: 'uncompressed-captured-signal-jsonl',
      fixtures: admitted.entries,
    };
    if (process.env.C3_PRINT_MANIFEST === '1') {
      console.log(`C3_MATRIX_MANIFEST\n${JSON.stringify(actualManifest, null, 2)}`);
      return;
    }
    assert.deepEqual(
      actualManifest,
      expectedManifest,
      'committed manifest must pin the complete exact fixture/receipt set',
    );

    // C1 composition negatives: missing, newline-truncated, and semantically
    // corrupt sources remain typed; a valid but changed tape cannot match its
    // committed content receipt.
    const negativeCustody = directorySignalCustody(join(scratch, 'negative-custody'));
    await assertTyped('missing-source', () => negativeCustody.admit({
      sourcePath: join(scratch, 'does-not-exist.jsonl'),
      expectedRecords: 1,
    }));
    const sourceDirectTeams = first.fixtures.find((fixture) => fixture.id === 'direct/teams');
    assert(sourceDirectTeams);
    const truncatedPath = join(scratch, 'truncated.jsonl');
    writeFileSync(
      truncatedPath,
      sourceDirectTeams.bytes.subarray(0, sourceDirectTeams.bytes.byteLength - 1),
    );
    await assertTyped('incomplete-source', () => negativeCustody.admit({
      sourcePath: truncatedPath,
      expectedRecords: sourceDirectTeams.records.length,
    }));
    const invalidRecords = sourceDirectTeams.records.map((record, index) =>
      index === 1
        ? { type: 'not-a-captured-signal-record', payload: 'semantic-corruption' }
        : record);
    const invalidPath = join(scratch, 'schema-invalid.jsonl');
    writeFileSync(
      invalidPath,
      capturedSignalBytes(sourceDirectTeams, invalidRecords as CapturedSignalRecord[]),
    );
    await assertTyped('incomplete-source', () => negativeCustody.admit({
      sourcePath: invalidPath,
      expectedRecords: sourceDirectTeams.records.length,
    }));

    const changedHeader = {
      ...sourceDirectTeams.records[0],
      native_meeting_id: 'valid-but-not-the-committed-fixture',
    } as CapturedSignalRecord;
    const changedPath = join(scratch, 'changed-valid.jsonl');
    writeFileSync(
      changedPath,
      capturedSignalBytes(
        sourceDirectTeams,
        [changedHeader, ...sourceDirectTeams.records.slice(1)],
      ),
    );
    const changedReceipt = await negativeCustody.admit({
      sourcePath: changedPath,
      expectedRecords: sourceDirectTeams.records.length,
    });
    assert.notEqual(
      changedReceipt.digest,
      expectedManifest.fixtures.find((entry) => entry.id === sourceDirectTeams.id)?.receipt.digest,
      'one valid changed byte must not satisfy the committed fixture receipt',
    );

    // All scorecards and controls start from the independently-read custody
    // bytes. Source truth remains external in the pinned authored timeline.
    const admittedFixtures = first.fixtures.map((fixture) => {
      const bytes = admitted.readback.get(fixture.id);
      assert(bytes, `${fixture.id}: admitted bytes missing`);
      return parseAdmittedFixture(fixture, bytes);
    });
    const directTeams = admittedFixtures.find((fixture) => fixture.id === 'direct/teams');
    const directJitsi = admittedFixtures.find((fixture) => fixture.id === 'direct/jitsi');
    assert(directTeams);
    assert(directJitsi);
    assert.equal(
      directTeams.scenario.turns.length,
      8,
      'direct Teams acceptance requires exactly eight authored turns',
    );
    assert.equal(
      directJitsi.scenario.turns.length,
      8,
      'direct Jitsi acceptance requires exactly eight authored turns',
    );

    const scores = new Map<string, ReplayScore>();
    for (const fixture of admittedFixtures) {
      const score = replayBinder(fixture);
      const secondScore = replayBinder(fixture);
      assert.deepEqual(secondScore, score, `${fixture.id}: replay must be deterministic`);
      assert.equal(score.invented, 0, `${fixture.id}: causally invented identities must be zero`);
      scores.set(fixture.id, score);

      if (fixture.platform === 'teams' || fixture.platform === 'zoom') {
        assert.equal(
          score.correct,
          score.turns,
          `${fixture.id}: admitted interval testimony must name every authored turn`,
        );
        assert(score.results.every((result) => result.source === 'window-match'));
      } else if (fixture.scenario.id === 'self-resume') {
        assert.equal(score.unknown, score.turns, 'Jitsi same-name resume has no distinct transition');
      } else {
        assert.equal(score.correct, score.turns - 1, `${fixture.id}: only baseline turn stays unknown`);
        assert.equal(score.unknown, 1, `${fixture.id}: baseline has no preceding transition`);
        assert(
          score.results
            .filter((result) => result.resolved === result.truth)
            .every((result) => result.source === 'exclusive-transition'),
        );
      }
    }

    // C-null: no testimony means zero display names on every producer contract.
    for (const fixture of admittedFixtures) {
      const score = replayBinder(fixture, []);
      assert.equal(score.unknown, score.turns, `${fixture.id}: C-null must stay fully unknown`);
    }

    // Teams timing/control falsifiers.
    const shiftedTeams = directTeams.hints.map((hint) => ({ ...hint, t: hint.t + 60_000 }));
    assert.equal(
      replayBinder(directTeams, shiftedTeams).unknown,
      directTeams.scenario.turns.length,
      'far-shifted outline hints cannot be recovered by nearest-name timing',
    );
    const overlapTeams = admittedFixtures.find((fixture) => fixture.id === 'overlap/teams');
    assert(overlapTeams);
    assert.equal(
      replayBinder(overlapTeams, overlapTeams.hints, 'jitsi').unknown,
      overlapTeams.scenario.turns.length,
      'concurrent Teams testimony cannot be reinterpreted as a global exclusive stream',
    );
    const swappedTeams = directTeams.hints.map((hint) => ({
      ...hint,
      name: hint.name === 'Anna' ? 'Boris' : hint.name === 'Boris' ? 'Anna' : hint.name,
    }));
    const swappedScore = replayBinder(directTeams, swappedTeams);
    assert.equal(swappedScore.unknown, 0);
    assert.equal(swappedScore.invented, swappedScore.turns, 'C-swap must follow hints, never audio/text');

    // Jitsi causal falsifiers.
    const baselineName = directJitsi.scenario.turns[0].speaker;
    const heartbeatOnly = directJitsi.hints.filter((hint) =>
      hint.name === baselineName && !hint.isEnd);
    assert.equal(
      replayBinder(directJitsi, heartbeatOnly).unknown,
      directJitsi.scenario.turns.length,
      'same-name heartbeats cannot mint transition testimony',
    );
    const mismatchedEnd = directJitsi.hints.map((hint) =>
      hint.isEnd ? { ...hint, name: 'CorruptEnd' } : hint);
    assert.equal(
      replayBinder(directJitsi, mismatchedEnd).unknown,
      directJitsi.scenario.turns.length,
      'mismatched exclusive end invalidates ordered custody',
    );
    const delayedJitsi = directJitsi.hints.map((hint) => ({ ...hint, t: hint.t + 5_000 }));
    const delayedScore = replayBinder(directJitsi, delayedJitsi);
    assert.equal(
      delayedScore.invented,
      0,
      'C-delay +5s may resolve or stay unknown, but cannot name the stale outgoing speaker: '
      + JSON.stringify(delayedScore.results),
    );
    assert.equal(
      delayedScore.unknown,
      delayedScore.turns,
      'C-delay +5s has an ambiguous leading/trailing alignment and must fully fail closed',
    );
    const nearDelayedJitsi = directJitsi.hints.map((hint) => ({ ...hint, t: hint.t + 1_000 }));
    const nearDelayedScore = replayBinder(directJitsi, nearDelayedJitsi);
    assert.equal(nearDelayedScore.correct, nearDelayedScore.turns - 1);
    assert.equal(nearDelayedScore.unknown, 1);
    assert.equal(nearDelayedScore.invented, 0);
    const swappedJitsi = directJitsi.hints.map((hint) => ({
      ...hint,
      name: hint.name === 'Anna' ? 'Boris' : hint.name === 'Boris' ? 'Anna' : hint.name,
    }));
    const swappedJitsiScore = replayBinder(directJitsi, swappedJitsi);
    assert.equal(swappedJitsiScore.unknown, 1);
    assert.equal(
      swappedJitsiScore.invented,
      swappedJitsiScore.turns - 1,
      'Jitsi C-swap must follow transition names, never audio or transcript text',
    );

    // Replay the independently-read direct tapes through the real mixed-lane
    // transcriber. Text is deterministic mock output; attribution, cuts, stable
    // segment ids, and repaint are production code.
    for (const fixture of [directTeams, directJitsi]) {
      const pipelineRun1 = await replayPipeline(fixture, fixture.bytes);
      const pipelineRun2 = await replayPipeline(fixture, fixture.bytes);
      assert.deepEqual(
        pipelineRun2,
        pipelineRun1,
        `${fixture.id}: real pipeline replay must be deterministic`,
      );
      const identity = pipelineIdentityScore(fixture, pipelineRun1);
      assert.equal(
        pipelineRun1.rows.length,
        8,
        `${fixture.id}: direct pipeline acceptance requires exactly eight rows`,
      );
      assert.equal(identity.unmatched, 0, `${fixture.id}: pipeline emitted an untimed row`);
      assert.equal(
        identity.correct + identity.unknown + identity.invented,
        pipelineRun1.rows.length,
        `${fixture.id}: every emitted row must be scored`,
      );
      assert.equal(identity.invented, 0, `${fixture.id}: pipeline replay invented a name`);
      const expectedRows = fixture.scenario.turns.map((turn, index) => ({
        id: `turn:${index}:0`,
        speaker: fixture.platform === 'jitsi' && index === 0
          ? turn.id
          : turn.speaker,
      }));
      assert.deepEqual(
        pipelineRun1.rows.map((row) => ({ id: row.id, speaker: row.speaker })),
        expectedRows,
        `${fixture.id}: direct pipeline identity/coverage floor drifted`,
      );
      if (fixture.platform === 'teams') {
        assert.equal(identity.correct, 8, 'direct Teams requires exactly eight correct rows');
        assert.equal(identity.unknown, 0);
        assert.deepEqual(pipelineRun1.renames, []);
      } else {
        assert.equal(identity.correct, 7, 'direct Jitsi requires exactly seven correct rows');
        assert.equal(identity.unknown, 1, 'direct Jitsi requires exactly one baseline unknown');
        assert.equal(
          pipelineRun1.renames.length,
          7,
          'direct Jitsi requires exactly seven stable-id repaints',
        );
        assert.deepEqual(
          pipelineRun1.renames,
          fixture.scenario.turns.slice(1).map((turn, index) => ({
            from: turn.id,
            to: turn.speaker,
            ids: [`turn:${index + 1}:0`],
          })),
          'Jitsi must repaint every causally admitted stable id, and only those ids',
        );
      }
    }

    // Zoom is intentionally pinned at the inherited boundary; no C3 producer
    // or causal-kind expansion is hidden inside fixture uniformity.
    assert.equal(hintKindForPlatform('teams'), 'dom-outline' satisfies HintKind);
    assert.equal(hintKindForPlatform('jitsi'), 'jitsi-dominant' satisfies HintKind);
    assert.equal(hintKindForPlatform('zoom'), 'dom-active' satisfies HintKind);
    const directScenario = first.scenarios.find((scenario) => scenario.id === 'direct');
    assert(directScenario);
    assert.throws(
      () => renderMatrixFixture('zoom', first.scenarios.find((scenario) => scenario.id === 'overlap')!),
      /direct non-regression only; overlap remains #797/,
    );

    const scoreLines = [...scores].map(([id, score]) => {
      const onsetP50 = percentile(score.authoredHintOnsetDelayMs, 0.5);
      const endP50 = percentile(score.authoredHintEndDelayMs, 0.5);
      const staleP50 = percentile(score.authoredHintStaleOutgoingMs, 0.5);
      return `${id}=correct:${score.correct}/${score.turns},unknown:${score.unknown},`
        + `invented:${score.invented},authored_hint_onset_p50:${onsetP50 ?? 'na'},`
        + `authored_hint_end_p50:${endP50 ?? 'na'},`
        + `authored_hint_stale_p50:${staleP50 ?? 'na'}`;
    }).join(' ');
    console.log(
      `C3_MATRIX_GREEN fixtures=${first.fixtures.length} `
      + `worker_deleted_before_readback=true receipts_exact=true idempotent=true `
      + `acoustic_identity=true null_control=true swap_control=true delay_control=true `
      + `pipeline_direct=teams+jitsi pipeline_deterministic=true stable_repaint=true `
      + `zoom_kind=dom-active ${scoreLines}`,
    );
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(message.startsWith('C3_MATRIX_RED') ? message : `C3_MATRIX_RED kind=unexpected ${message}`);
  process.exitCode = 4;
});

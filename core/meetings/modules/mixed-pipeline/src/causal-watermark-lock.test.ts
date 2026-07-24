/**
 * #956 M2 causal watermark contract.
 *
 * This authored Jitsi fixture drives 28 neutral 250 ms PCM frames through the
 * real ChunkedTranscriber. Deferred STT barriers make the two adjudication
 * instants deterministic without wall-clock sleeps:
 *
 *   1. the open turn publishes while Jitsi still reports outgoing Anna;
 *   2. the same stable segment id confirms provisionally after the distinct
 *      Boris transition but before signal progress beyond the closed turn;
 *   3. only the later progress event repaints that already-confirmed id.
 *
 * Causal contract:
 *   - an exclusive-trailing heartbeat is liveness, not onset testimony;
 *   - the open turn stays repairable and publishes under its provisional id;
 *   - after the closed turn has one uniquely ordered transition plus stream
 *     progress beyond its end, that same id repaints to Boris;
 *   - omitting only the final progress event leaves the turn provisional.
 *
 * The authored PCM is identity-neutral: attribution uses only boundary and
 * platform-signal order.
 */
import assert from 'node:assert/strict';
import {
  ChunkedTranscriber,
  ClusterNameBinder,
  type BoundaryEvent,
  type BoundarySource,
  type ChunkSegment,
} from './index.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

const SAMPLE_RATE = 16_000;
const FRAME_MS = 250;
const ONSET_MS = 1_718_000_005_000;

interface Deferred {
  promise: Promise<void>;
  resolve: () => void;
}

interface Write {
  via: 'pending' | 'confirmed';
  speaker: string;
  segment: ChunkSegment;
}

interface Rename {
  oldSpeaker: string;
  newSpeaker: string;
  segments: ChunkSegment[];
}

interface RunResult {
  writes: Write[];
  renames: Rename[];
  materialized: Map<string, { speaker: string; text: string }>;
}

function deferred(): Deferred {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

function authoredResult(pcm: Float32Array): TranscriptionResult {
  const duration = pcm.length / SAMPLE_RATE;
  return {
    text: 'authored neutral utterance',
    language: 'en',
    language_probability: 0.99,
    duration,
    segments: [{
      start: 0,
      end: duration,
      text: 'authored neutral utterance',
      no_speech_prob: 0,
      avg_logprob: -0.1,
      compression_ratio: 1,
    }],
  } as TranscriptionResult;
}

async function runAuthoredTurn(
  withFinalProgress: boolean,
  withLateContradiction = false,
): Promise<RunResult> {
  const entered = [deferred(), deferred()];
  const release = [deferred(), deferred()];
  const firstPending = deferred();
  const finalConfirmed = deferred();
  const finalRename = deferred();
  const writes: Write[] = [];
  const renames: Rename[] = [];
  let sttCall = 0;
  let emit!: (event: BoundaryEvent) => void;

  const transcriber = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async (pcm) => {
      const call = sttCall++;
      assert(call < 2, `unexpected STT call ${call + 1}`);
      entered[call].resolve();
      await release[call].promise;
      return authoredResult(pcm);
    },
    publish: (speaker, confirmed, pending) => {
      for (const segment of confirmed) writes.push({ via: 'confirmed', speaker, segment });
      for (const segment of pending) writes.push({ via: 'pending', speaker, segment });
      if (confirmed.length > 0) finalConfirmed.resolve();
    },
    publishPending: (speaker, pending) => {
      for (const segment of pending) writes.push({ via: 'pending', speaker, segment });
      if (pending.length > 0) firstPending.resolve();
    },
    clearPending: () => {},
    rename: (oldSpeaker, newSpeaker, segments) => {
      renames.push({ oldSpeaker, newSpeaker, segments: [...segments] });
      finalRename.resolve();
    },
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
      emit = onBoundary;
      return { appendFrame: async () => {}, reset() {} };
    },
    log: () => {},
  });

  const feedRange = (fromMs: number, toMs: number): void => {
    for (let offsetMs = fromMs; offsetMs < toMs; offsetMs += FRAME_MS) {
      const pcm = new Float32Array((SAMPLE_RATE * FRAME_MS) / 1000);
      pcm.fill(0.1);
      transcriber.feedAudio(pcm, ONSET_MS + offsetMs);
    }
  };

  // The prior transition is real. The same-name +200 event is only Jitsi's
  // periodic reassertion of a smoothed global state.
  transcriber.recordHint('Anna', 'jitsi-dominant', ONSET_MS - 5000);
  feedRange(0, 2000);
  emit({ kind: 'silence→speaker', tMs: ONSET_MS, confidence: 0.9 });
  await entered[0].promise;
  transcriber.recordHint('Anna', 'jitsi-dominant', ONSET_MS + 200);
  release[0].resolve();
  await firstPending.promise;

  feedRange(2000, 2750);
  transcriber.recordHint('Anna', 'jitsi-dominant', ONSET_MS + 2803, true);
  transcriber.recordHint('Boris', 'jitsi-dominant', ONSET_MS + 2803);
  feedRange(2750, 4750);
  transcriber.recordHint('Boris', 'jitsi-dominant', ONSET_MS + 4803);
  feedRange(4750, 6750);
  transcriber.recordHint('Boris', 'jitsi-dominant', ONSET_MS + 6803);
  feedRange(6750, 7000);

  emit({ kind: 'speaker→silence', tMs: ONSET_MS + 7000, confidence: 0.9 });
  await entered[1].promise;
  release[1].resolve();
  await finalConfirmed.promise;
  if (withFinalProgress) {
    transcriber.recordHint('Boris', 'jitsi-dominant', ONSET_MS + 8000, true);
    await finalRename.promise;
  }
  if (withLateContradiction) {
    assert(withFinalProgress, 'post-lock contradiction requires a locked attribution');
    // Delayed heartbeats from both the outgoing epoch and the just-ended
    // current epoch are safe when their own timestamps preserve that order.
    transcriber.recordHint('Anna', 'jitsi-dominant', ONSET_MS + 2700);
    transcriber.recordHint('Boris', 'jitsi-dominant', ONSET_MS + 7000);
    assert.equal(renames.length, 1);
    // A distinct late identity proves ordered custody was incomplete. Repaint
    // the already-published stable id to provisional, never to Charlie.
    transcriber.recordHint('Charlie', 'jitsi-dominant', ONSET_MS + 2700);
    transcriber.recordHint('Charlie', 'jitsi-dominant', ONSET_MS + 2600);
  }
  await transcriber.dispose();

  assert.equal(sttCall, 2);
  const materialized = new Map<string, { speaker: string; text: string }>();
  for (const write of writes) {
    materialized.set(write.segment.segmentId, {
      speaker: write.speaker,
      text: write.segment.text,
    });
  }
  for (const rename of renames) {
    for (const segment of rename.segments) {
      materialized.set(segment.segmentId, {
        speaker: rename.newSpeaker,
        text: segment.text,
      });
    }
  }
  return { writes, renames, materialized };
}

/** Production-seam control for canonical M1: the transition is first observed
 * after a short turn has closed. It cannot repaint immediately because acoustic
 * custody is sealed only through the turn end. The next real acoustic onset
 * seals the intervening silence beyond the token and triggers exactly one
 * provisional→Boris repaint before registering the resumed turn. */
async function runPostEndRepairAtNextBoundary(): Promise<Rename[]> {
  const entered = [deferred(), deferred()];
  const release = [deferred(), deferred()];
  const pendingPublished = deferred();
  const finalConfirmed = deferred();
  const repainted = deferred();
  const renames: Rename[] = [];
  let sttCall = 0;
  let emit!: (event: BoundaryEvent) => void;

  const transcriber = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async (pcm) => {
      const call = sttCall++;
      assert(call < 2, `unexpected post-end STT call ${call + 1}`);
      entered[call].resolve();
      await release[call].promise;
      return authoredResult(pcm);
    },
    publish: (_speaker, confirmed) => {
      if (confirmed.length > 0) finalConfirmed.resolve();
    },
    publishPending: (_speaker, pending) => {
      if (pending.length > 0) pendingPublished.resolve();
    },
    clearPending: () => {},
    rename: (oldSpeaker, newSpeaker, segments) => {
      renames.push({ oldSpeaker, newSpeaker, segments: [...segments] });
      repainted.resolve();
    },
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
      emit = onBoundary;
      return { appendFrame: async () => {}, reset() {} };
    },
    log: () => {},
  });

  const feedRange = (fromMs: number, toMs: number, amplitude: number): void => {
    for (let offsetMs = fromMs; offsetMs < toMs; offsetMs += FRAME_MS) {
      const pcm = new Float32Array((SAMPLE_RATE * FRAME_MS) / 1000);
      pcm.fill(amplitude);
      transcriber.feedAudio(pcm, ONSET_MS + offsetMs);
    }
  };

  transcriber.recordHint('Anna', 'jitsi-dominant', ONSET_MS - 5000);
  feedRange(0, 2000, 0.1);
  emit({ kind: 'silence→speaker', tMs: ONSET_MS, confidence: 0.9 });
  await entered[0].promise;
  transcriber.recordHint('Anna', 'jitsi-dominant', ONSET_MS + 200);
  release[0].resolve();
  await pendingPublished.promise;

  emit({ kind: 'speaker→silence', tMs: ONSET_MS + 2000, confidence: 0.9 });
  await entered[1].promise;
  release[1].resolve();
  await finalConfirmed.promise;

  transcriber.recordHint('Anna', 'jitsi-dominant', ONSET_MS + 2803, true);
  transcriber.recordHint('Boris', 'jitsi-dominant', ONSET_MS + 2803);
  assert.deepEqual(renames, [], 'post-end token waits for acoustic custody');

  // Captured silence advances the audio clock, but only the next boundary is
  // authoritative acoustic custody. Its onset seals the no-turn interval.
  feedRange(2000, 5000, 0);
  emit({ kind: 'silence→speaker', tMs: ONSET_MS + 5000, confidence: 0.9 });
  await repainted.promise;
  await transcriber.dispose();
  assert.equal(sttCall, 2);
  return renames;
}

const diagnostic = new ClusterNameBinder({});
const hint = (name: string, offsetMs: number, isEnd = false): void => {
  diagnostic.recordHint({
    name,
    tMs: ONSET_MS + offsetMs,
    kind: 'jitsi-dominant',
    isEnd,
  });
};
hint('Anna', -5000);
hint('Anna', 200);
const earlyExplain = diagnostic.resolve(
  { clusterId: 'seg_0', tStartMs: ONSET_MS, tEndMs: ONSET_MS },
  { recordVote: false, finalized: false },
);
hint('Anna', 2803, true);
hint('Boris', 2803);
hint('Boris', 4803);
hint('Boris', 6803);
hint('Boris', 8000, true);
diagnostic.registerAcousticTurn(
  { clusterId: 'seg_0', tStartMs: ONSET_MS, tEndMs: ONSET_MS + 7000 },
  true,
);
diagnostic.sealAcousticThrough(ONSET_MS + 7000);
const fullExplain = diagnostic.resolve(
  { clusterId: 'seg_0', tStartMs: ONSET_MS, tEndMs: ONSET_MS + 7000 },
  { recordVote: false, finalized: true },
);
assert.equal(
  fullExplain.speakerName,
  'Boris',
  'the closed turn plus ordered transition and final progress admits Boris',
);

const progressed = await runAuthoredTurn(true);
const noProgress = await runAuthoredTurn(false);
const invalidated = await runAuthoredTurn(true, true);
const postEndRepair = await runPostEndRepairAtNextBoundary();

const nonempty = [...progressed.materialized.entries()].filter(([, row]) => row.text);
const stableIds = new Set(
  progressed.writes
    .filter((write) => write.segment.text)
    .map((write) => write.segment.segmentId),
);
const personWrites = progressed.writes.filter(
  (write) => !/^seg_\d+$/.test(write.speaker),
);
const earlyWrite = progressed.writes.find(
  (write) => write.via === 'pending' && write.segment.text,
);
const noProgressNames = [...noProgress.materialized.values()]
  .filter((row) => row.text)
  .map((row) => row.speaker);
const repaint = progressed.renames.filter((rename) =>
  /^seg_\d+$/.test(rename.oldSpeaker)
  && rename.newSpeaker === 'Boris'
  && rename.segments.some((segment) => segment.text));
const invalidatedIds = new Set(
  invalidated.writes
    .filter((write) => write.segment.text)
    .map((write) => write.segment.segmentId),
);
const rollback = invalidated.renames[1];
const invalidatedNames = [...invalidated.materialized.values()]
  .filter((row) => row.text)
  .map((row) => row.speaker);

const causalGreen =
  earlyWrite !== undefined
  && /^seg_\d+$/.test(earlyWrite.speaker)
  && personWrites.length === 0
  && stableIds.size === 1
  && nonempty.length === 1
  && nonempty[0][1].speaker === 'Boris'
  && progressed.renames.length === 1
  && repaint.length === 1
  && repaint[0].segments.every((segment) => stableIds.has(segment.segmentId))
  && noProgress.renames.length === 0
  && noProgressNames.length === 1
  && /^seg_\d+$/.test(noProgressNames[0])
  && invalidated.renames.length === 2
  && invalidated.renames[0].oldSpeaker === 'seg_0'
  && invalidated.renames[0].newSpeaker === 'Boris'
  && rollback?.oldSpeaker === 'Boris'
  && rollback.newSpeaker === 'seg_0'
  && rollback.segments.every((segment) => invalidatedIds.has(segment.segmentId))
  && invalidatedNames.length === 1
  && invalidatedNames[0] === 'seg_0'
  && postEndRepair.length === 1
  && /^seg_\d+$/.test(postEndRepair[0].oldSpeaker)
  && postEndRepair[0].newSpeaker === 'Boris';

const writeSummary = progressed.writes
  .filter((write) => write.segment.text)
  .map((write) => `${write.via}:${write.speaker}:${write.segment.segmentId}`)
  .join(',');
const renameSummary = progressed.renames
  .map((rename) => `${rename.oldSpeaker}->${rename.newSpeaker}`)
  .join(',') || 'none';

console.log(
  `M2_WATERMARK_${causalGreen ? 'GREEN' : 'RED'} `
  + `early_explain=${earlyExplain.speakerName}/${earlyExplain.source}/${earlyExplain.confidence.toFixed(3)} `
  + `full_explain=${fullExplain.speakerName}/${fullExplain.source}/${fullExplain.confidence.toFixed(3)} `
  + `writes=${writeSummary} renames=${renameSummary} `
  + `no_progress=${noProgressNames.join(',') || 'none'} `
  + `late_contradiction=${invalidated.renames.map((rename) =>
    `${rename.oldSpeaker}->${rename.newSpeaker}`).join(',')} `
  + `post_end_repair=${postEndRepair.map((rename) =>
    `${rename.oldSpeaker}->${rename.newSpeaker}`).join(',') || 'none'}`,
);
process.exitCode = causalGreen ? 0 : 1;

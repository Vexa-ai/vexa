/**
 * #956 M1 causal contract.
 *
 * The authored captured-signal tape models Jitsi's exclusive, trailing
 * dominant-speaker signal. Boris's acoustic turn starts while Redux still
 * reports Anna; the repeated Anna event is therefore a heartbeat ("no
 * transition detected"), not fresh testimony that Anna started this turn.
 * Jitsi reports the Anna→Boris transition 2,803 ms after acoustic onset,
 * after this authored 2,000 ms turn has already ended.
 *
 * Causal contract:
 *   - before that exclusive signal advances, the turn is unknown;
 *   - the first valid transition may repair it only after acoustic custody is
 *     independently sealed beyond that transition;
 *   - a name is admitted only when exactly one turn started in the transition's
 *     signal epoch and exactly one transition can claim that turn;
 *   - missing, compacted, multi-token, or out-of-order custody fails closed.
 *
 * The authored PCM/tape is identity-neutral: attribution uses only boundary
 * and producer-signal order.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  ClusterNameBinder,
  type CommitInfo,
  type HintEvent,
} from './index.js';

interface TapeHint {
  type: 'hint';
  t: number;
  name: string;
  isEnd?: boolean;
}

interface TapeBoundary {
  type: 'boundary';
  tMs: number;
  kind: string;
}

const here = dirname(fileURLToPath(import.meta.url));
const tapePath = join(
  here,
  '..',
  '..',
  '..',
  'eval',
  'replay-fixture',
  'm1-jitsi-stale-heartbeat.captured-signal.jsonl',
);
const records = readFileSync(tapePath, 'utf8')
  .trim()
  .split('\n')
  .map((line) => JSON.parse(line) as Record<string, unknown>);
const header = records[0];
assert.equal(header.type, 'captured_signal_header');
assert.equal(header.platform, 'jitsi');
assert.equal(header.lane, 'mixed');

const hints = records.filter((record) => record.type === 'hint') as unknown as TapeHint[];
const boundaries = records.filter(
  (record) => record.type === 'boundary',
) as unknown as TapeBoundary[];
const onset = boundaries.find((record) => record.kind === 'speaker→speaker')?.tMs;
const end = boundaries.find((record) => record.kind === 'speaker→silence')?.tMs;
const correctStart = hints.find((hint) => hint.name === 'Boris' && !hint.isEnd)?.t;
assert(onset !== undefined && end !== undefined && correctStart !== undefined);
assert.equal(correctStart - onset, 2803, 'authored onset→identity-start envelope');

const commit = { clusterId: 'seg_boris_turn', tStartMs: onset, tEndMs: end };
const asCurrentHint = (hint: TapeHint): HintEvent => ({
  name: hint.name,
  tMs: hint.t,
  kind: 'jitsi-dominant',
  isEnd: hint.isEnd,
});
const registerAndSeal = (
  binder: ClusterNameBinder,
  turns: CommitInfo[],
  throughMs = Math.max(...turns.map((turn) => turn.tEndMs)),
): void => {
  for (const turn of turns) binder.registerAcousticTurn(turn, true);
  binder.sealAcousticThrough(throughMs);
};
const progress = (
  binder: ClusterNameBinder,
  name: string,
  tMs: number,
): void => binder.recordHint({
  name,
  tMs,
  kind: 'jitsi-dominant',
  isEnd: true,
});

// C-null: absence of testimony cannot invent a display name.
const nullControl = new ClusterNameBinder({}).resolve(commit, { recordVote: false });
assert.equal(nullControl.speakerName, commit.clusterId);
assert.equal(nullControl.source, 'provisional-cluster-id');

// Removing only the repeated heartbeat still leaves no transition testimony.
const withoutHeartbeatBinder = new ClusterNameBinder({});
const initialOutgoingHint = hints.find((hint) => hint.name === 'Anna' && !hint.isEnd);
assert(initialOutgoingHint);
withoutHeartbeatBinder.recordHint(asCurrentHint(initialOutgoingHint));
registerAndSeal(withoutHeartbeatBinder, [commit]);
const withoutHeartbeat = withoutHeartbeatBinder.resolve(commit, { recordVote: false });
assert.equal(withoutHeartbeat.speakerName, commit.clusterId);

const binder = new ClusterNameBinder({});
for (const hint of hints.filter((event) => event.t < correctStart)) {
  binder.recordHint(asCurrentHint(hint));
}
registerAndSeal(binder, [commit]);
const beforeAdvance = binder.resolve(commit, { recordVote: false });
for (const hint of hints.filter((event) => event.t >= correctStart)) {
  binder.recordHint(asCurrentHint(hint));
}
binder.sealAcousticThrough(correctStart + 1);
const afterAdvance = binder.resolve(commit, { recordVote: false });
assert.equal(beforeAdvance.source, 'provisional-cluster-id');
assert.equal(afterAdvance.speakerName, 'Boris');

// A heartbeat may arrive after the acoustic turn ends while Jitsi's smoothed
// global state is still stale. Progress alone cannot admit Anna. The later
// transition repairs to Boris only after the acoustic timeline is separately
// sealed beyond that token.
const postEnd = new ClusterNameBinder({});
postEnd.recordHint({
  name: 'Anna',
  tMs: onset - 5000,
  kind: 'jitsi-dominant',
});
registerAndSeal(postEnd, [commit]);
postEnd.recordHint({
  name: 'Anna',
  tMs: end + 200,
  kind: 'jitsi-dominant',
});
const afterPostEndHeartbeat = postEnd.resolve(commit, { recordVote: false });
postEnd.recordHint({
  name: 'Anna',
  tMs: correctStart,
  kind: 'jitsi-dominant',
  isEnd: true,
});
postEnd.recordHint({
  name: 'Boris',
  tMs: correctStart,
  kind: 'jitsi-dominant',
});
postEnd.sealAcousticThrough(correctStart + 1);
const afterPostEndTransition = postEnd.resolve(commit, { recordVote: false });
assert.equal(afterPostEndHeartbeat.source, 'provisional-cluster-id');
assert.equal(afterPostEndTransition.speakerName, 'Boris');

// One transition contained by two registered turns is not globally unique.
const overlapping = new ClusterNameBinder({});
const overlappingA = {
  clusterId: 'seg_overlap_a',
  tStartMs: onset,
  tEndMs: onset + 6000,
};
const overlappingB = {
  clusterId: 'seg_overlap_b',
  tStartMs: onset + 1000,
  tEndMs: onset + 5000,
};
overlapping.recordHint({
  name: 'Anna',
  tMs: onset - 5000,
  kind: 'jitsi-dominant',
});
registerAndSeal(overlapping, [overlappingA, overlappingB], onset + 6000);
overlapping.recordHint({
  name: 'Anna',
  tMs: correctStart,
  kind: 'jitsi-dominant',
  isEnd: true,
});
overlapping.recordHint({
  name: 'Boris',
  tMs: correctStart,
  kind: 'jitsi-dominant',
});
progress(overlapping, 'Boris', onset + 7000);
const overlappingResultA = overlapping.resolve(overlappingA, { recordVote: false });
const overlappingResultB = overlapping.resolve(overlappingB, { recordVote: false });
assert.equal(overlappingResultA.source, 'provisional-cluster-id');
assert.equal(overlappingResultB.source, 'provisional-cluster-id');

// Two transitions contained by one acoustic turn are likewise ambiguous.
const multiToken = new ClusterNameBinder({});
const multiTokenTurn = {
  clusterId: 'seg_multi_token',
  tStartMs: onset,
  tEndMs: onset + 5000,
};
multiToken.recordHint({ name: 'Anna', tMs: onset - 5000, kind: 'jitsi-dominant' });
registerAndSeal(multiToken, [multiTokenTurn]);
multiToken.recordHint({
  name: 'Anna',
  tMs: onset + 1000,
  kind: 'jitsi-dominant',
  isEnd: true,
});
multiToken.recordHint({ name: 'Boris', tMs: onset + 1000, kind: 'jitsi-dominant' });
multiToken.recordHint({
  name: 'Boris',
  tMs: onset + 3000,
  kind: 'jitsi-dominant',
  isEnd: true,
});
multiToken.recordHint({ name: 'Carol', tMs: onset + 3000, kind: 'jitsi-dominant' });
progress(multiToken, 'Carol', onset + 6000);
const multiTokenResult = multiToken.resolve(multiTokenTurn, { recordVote: false });
assert.equal(multiTokenResult.source, 'provisional-cluster-id');

// Acoustic compaction is an immutable missing-history tombstone. Removing an
// older candidate can never make the retained turn appear spuriously unique.
const acousticCompacted = new ClusterNameBinder({ hintLogLimit: 1 });
const compactedOldTurn = {
  clusterId: 'seg_compacted_old',
  tStartMs: onset - 4000,
  tEndMs: onset - 3000,
};
const compactedCurrentTurn = {
  clusterId: 'seg_compacted_current',
  tStartMs: onset,
  tEndMs: onset + 2000,
};
acousticCompacted.recordHint({
  name: 'Anna',
  tMs: onset - 5000,
  kind: 'jitsi-dominant',
});
registerAndSeal(
  acousticCompacted,
  [compactedOldTurn, compactedCurrentTurn],
  onset + 3000,
);
acousticCompacted.recordHint({
  name: 'Anna',
  tMs: onset + 1000,
  kind: 'jitsi-dominant',
  isEnd: true,
});
acousticCompacted.recordHint({
  name: 'Boris',
  tMs: onset + 1000,
  kind: 'jitsi-dominant',
});
progress(acousticCompacted, 'Boris', onset + 3000);
const acousticCompactedResult = acousticCompacted.resolve(
  compactedCurrentTurn,
  { recordVote: false },
);
assert.equal(acousticCompactedResult.source, 'provisional-cluster-id');

// Transition compaction has an independent tombstone. With only the retained
// Carol token, this turn would look uniquely attributable; the missing Boris
// transition keeps the whole session unknown.
const transitionCompacted = new ClusterNameBinder({ hintLogLimit: 1 });
const transitionCompactedTurn = {
  clusterId: 'seg_transition_compacted',
  tStartMs: onset,
  tEndMs: onset + 2000,
};
transitionCompacted.recordHint({
  name: 'Anna',
  tMs: onset - 6000,
  kind: 'jitsi-dominant',
});
registerAndSeal(transitionCompacted, [transitionCompactedTurn], onset + 3000);
transitionCompacted.recordHint({
  name: 'Anna',
  tMs: onset - 1000,
  kind: 'jitsi-dominant',
  isEnd: true,
});
transitionCompacted.recordHint({
  name: 'Boris',
  tMs: onset - 1000,
  kind: 'jitsi-dominant',
});
transitionCompacted.recordHint({
  name: 'Boris',
  tMs: onset + 1000,
  kind: 'jitsi-dominant',
  isEnd: true,
});
transitionCompacted.recordHint({
  name: 'Carol',
  tMs: onset + 1000,
  kind: 'jitsi-dominant',
});
progress(transitionCompacted, 'Carol', onset + 3000);
const transitionCompactedResult = transitionCompacted.resolve(
  transitionCompactedTurn,
  { recordVote: false },
);
assert.equal(transitionCompactedResult.source, 'provisional-cluster-id');

// C-swap: swapping only signal names swaps the admitted identity. This longer
// turn contains one token and proves the binder never reads transcript text.
const longTurn = {
  clusterId: 'seg_long',
  tStartMs: onset,
  tEndMs: onset + 7000,
};
const swapped = new ClusterNameBinder({});
swapped.recordHint({ name: 'Boris', tMs: onset - 5000, kind: 'jitsi-dominant' });
registerAndSeal(swapped, [longTurn]);
swapped.recordHint({
  name: 'Boris',
  tMs: correctStart,
  kind: 'jitsi-dominant',
  isEnd: true,
});
swapped.recordHint({ name: 'Anna', tMs: correctStart, kind: 'jitsi-dominant' });
progress(swapped, 'Anna', onset + 8000);
const swappedResult = swapped.resolve(longTurn, { recordVote: false });
assert.equal(swappedResult.speakerName, 'Anna');

// C-delay: moving the same ordered transition later does not change its
// identity. It can repair to Boris only after acoustic custody seals past it;
// it can never produce the stale outgoing Anna.
const delayed = new ClusterNameBinder({});
delayed.recordHint({ name: 'Anna', tMs: onset - 5000, kind: 'jitsi-dominant' });
registerAndSeal(delayed, [commit]);
delayed.recordHint({
  name: 'Anna',
  tMs: onset + 5000,
  kind: 'jitsi-dominant',
  isEnd: true,
});
delayed.recordHint({ name: 'Boris', tMs: onset + 5000, kind: 'jitsi-dominant' });
progress(delayed, 'Boris', onset + 6000);
delayed.sealAcousticThrough(onset + 6001);
const delayedResult = delayed.resolve(commit, { recordVote: false });
assert.equal(delayedResult.speakerName, 'Boris');

// An older heartbeat for the just-outgoing state cannot reverse a valid
// transition. A distinct late name is contradictory testimony and fails closed.
const ordered = new ClusterNameBinder({});
ordered.recordHint({ name: 'Anna', tMs: onset - 5000, kind: 'jitsi-dominant' });
registerAndSeal(ordered, [longTurn]);
ordered.recordHint({
  name: 'Anna',
  tMs: correctStart,
  kind: 'jitsi-dominant',
  isEnd: true,
});
ordered.recordHint({ name: 'Boris', tMs: correctStart, kind: 'jitsi-dominant' });
progress(ordered, 'Boris', onset + 8000);
ordered.recordHint({ name: 'Anna', tMs: correctStart - 100, kind: 'jitsi-dominant' });
const afterOutOfOrderHeartbeat = ordered.resolve(longTurn, { recordVote: false });
assert.equal(afterOutOfOrderHeartbeat.speakerName, 'Boris');

const contradictory = new ClusterNameBinder({});
contradictory.recordHint({ name: 'Anna', tMs: onset - 5000, kind: 'jitsi-dominant' });
registerAndSeal(contradictory, [longTurn]);
contradictory.recordHint({
  name: 'Anna',
  tMs: correctStart,
  kind: 'jitsi-dominant',
  isEnd: true,
});
contradictory.recordHint({ name: 'Boris', tMs: correctStart, kind: 'jitsi-dominant' });
progress(contradictory, 'Boris', onset + 8000);
contradictory.recordHint({
  name: 'Charlie',
  tMs: correctStart - 100,
  kind: 'jitsi-dominant',
});
const contradictoryResult = contradictory.resolve(longTurn, { recordVote: false });
assert.equal(contradictoryResult.source, 'provisional-cluster-id');

// Even the outgoing name is no longer a harmless stale heartbeat when its own
// timestamp is after the accepted transition. That is contradictory testimony.
const lateOutgoingAfterTransition = new ClusterNameBinder({});
lateOutgoingAfterTransition.recordHint({
  name: 'Anna',
  tMs: onset - 5000,
  kind: 'jitsi-dominant',
});
registerAndSeal(lateOutgoingAfterTransition, [longTurn]);
lateOutgoingAfterTransition.recordHint({
  name: 'Anna',
  tMs: correctStart,
  kind: 'jitsi-dominant',
  isEnd: true,
});
lateOutgoingAfterTransition.recordHint({
  name: 'Boris',
  tMs: correctStart,
  kind: 'jitsi-dominant',
});
progress(lateOutgoingAfterTransition, 'Boris', onset + 8000);
lateOutgoingAfterTransition.recordHint({
  name: 'Anna',
  tMs: correctStart + 1000,
  kind: 'jitsi-dominant',
});
const lateOutgoingAfterTransitionResult = lateOutgoingAfterTransition.resolve(
  longTurn,
  { recordVote: false },
);
assert.equal(
  lateOutgoingAfterTransitionResult.source,
  'provisional-cluster-id',
);

// An end token for a name that never owned the current global state is corrupt
// custody, not signal progress. It cannot provide the missing post-turn
// watermark that would otherwise authorize the preceding Boris transition.
const mismatchedEndCallbacks: string[] = [];
const mismatchedEnd = new ClusterNameBinder({
  onLateResolve: (_clusterId, name) => mismatchedEndCallbacks.push(name),
});
mismatchedEnd.recordHint({
  name: 'Anna',
  tMs: onset - 5000,
  kind: 'jitsi-dominant',
});
registerAndSeal(mismatchedEnd, [longTurn]);
mismatchedEnd.recordHint({
  name: 'Anna',
  tMs: correctStart,
  kind: 'jitsi-dominant',
  isEnd: true,
});
mismatchedEnd.recordHint({
  name: 'Boris',
  tMs: correctStart,
  kind: 'jitsi-dominant',
});
const beforeMismatchedEnd = mismatchedEnd.resolve(longTurn);
mismatchedEnd.recordHint({
  name: 'Charlie',
  tMs: onset + 8000,
  kind: 'jitsi-dominant',
  isEnd: true,
});
const afterMismatchedEnd = mismatchedEnd.resolve(longTurn);
assert.equal(beforeMismatchedEnd.source, 'provisional-cluster-id');
assert.equal(afterMismatchedEnd.source, 'provisional-cluster-id');
assert.deepEqual(mismatchedEndCallbacks, []);

// C-self is a real resume: a prior closed turn, silence, then a new acoustic
// turn with an explicit Anna end→start transition. With both turns in one
// unresolved signal epoch, the conservative result is unknown—not a fabricated
// second identity and not an unproven same-name carry.
const sameSpeakerResume = new ClusterNameBinder({});
const priorTurn = {
  clusterId: 'seg_prior_anna',
  tStartMs: onset - 4000,
  tEndMs: onset - 2000,
};
const resumedTurn = {
  clusterId: 'seg_resumed_anna',
  tStartMs: onset,
  tEndMs: onset + 5000,
};
sameSpeakerResume.recordHint({
  name: 'Anna',
  tMs: onset - 5000,
  kind: 'jitsi-dominant',
});
registerAndSeal(sameSpeakerResume, [priorTurn], priorTurn.tEndMs);
sameSpeakerResume.registerAcousticTurn(resumedTurn, true);
sameSpeakerResume.sealAcousticThrough(resumedTurn.tEndMs);
sameSpeakerResume.recordHint({
  name: 'Anna',
  tMs: onset + 1000,
  kind: 'jitsi-dominant',
  isEnd: true,
});
sameSpeakerResume.recordHint({
  name: 'Anna',
  tMs: onset + 1000,
  kind: 'jitsi-dominant',
});
progress(sameSpeakerResume, 'Anna', onset + 6000);
const resumeResult = sameSpeakerResume.resolve(resumedTurn, { recordVote: false });
assert.equal(resumeResult.source, 'provisional-cluster-id');

// The review's exact late-registration sequence stays unknown at every point:
// the token is after seg_a and cannot be reused for the later short seg_b.
const lateRegistration = new ClusterNameBinder({});
const lateA = {
  clusterId: 'seg_late_a',
  tStartMs: onset + 1000,
  tEndMs: onset + 2000,
};
const lateB = {
  clusterId: 'seg_late_b',
  tStartMs: onset + 2500,
  tEndMs: onset + 2700,
};
lateRegistration.recordHint({
  name: 'Anna',
  tMs: onset,
  kind: 'jitsi-dominant',
});
registerAndSeal(lateRegistration, [lateA]);
lateRegistration.recordHint({
  name: 'Anna',
  tMs: onset + 3000,
  kind: 'jitsi-dominant',
  isEnd: true,
});
lateRegistration.recordHint({
  name: 'Boris',
  tMs: onset + 3000,
  kind: 'jitsi-dominant',
});
progress(lateRegistration, 'Boris', onset + 4000);
const lateABefore = lateRegistration.resolve(lateA, { recordVote: false });
lateRegistration.registerAcousticTurn(lateB, true);
lateRegistration.sealAcousticThrough(lateB.tEndMs);
const lateAStillUnsealed = lateRegistration.resolve(lateA, { recordVote: false });
const lateBStillUnsealed = lateRegistration.resolve(lateB, { recordVote: false });
lateRegistration.sealAcousticThrough(onset + 4001);
const lateAAfter = lateRegistration.resolve(lateA, { recordVote: false });
const lateBAfter = lateRegistration.resolve(lateB, { recordVote: false });
assert.equal(lateABefore.source, 'provisional-cluster-id');
assert.equal(lateAStillUnsealed.source, 'provisional-cluster-id');
assert.equal(lateBStillUnsealed.source, 'provisional-cluster-id');
assert.equal(lateAAfter.source, 'provisional-cluster-id');
assert.equal(lateBAfter.source, 'provisional-cluster-id');

// A single candidate may lock only after the acoustic seal passes its token.
const uniqueCallbacks: string[] = [];
const sealedUnique = new ClusterNameBinder({
  onLateResolve: (_clusterId, name) => uniqueCallbacks.push(name),
});
sealedUnique.recordHint({ name: 'Anna', tMs: onset, kind: 'jitsi-dominant' });
registerAndSeal(sealedUnique, [lateA]);
sealedUnique.recordHint({
  name: 'Anna',
  tMs: onset + 3000,
  kind: 'jitsi-dominant',
  isEnd: true,
});
sealedUnique.recordHint({
  name: 'Boris',
  tMs: onset + 3000,
  kind: 'jitsi-dominant',
});
progress(sealedUnique, 'Boris', onset + 4000);
const uniqueBeforeSeal = sealedUnique.resolve(lateA);
assert.equal(uniqueBeforeSeal.source, 'provisional-cluster-id');
assert.deepEqual(uniqueCallbacks, []);
sealedUnique.sealAcousticThrough(onset + 4001);
const uniqueAfterSeal = sealedUnique.resolve(lateA);
assert.equal(uniqueAfterSeal.speakerName, 'Boris');
assert.deepEqual(uniqueCallbacks, ['Boris']);
// A stale observation of the outgoing state before the accepted transition is
// still harmless after lock. A different late identity contradicts custody and
// must revoke the published name exactly once.
sealedUnique.recordHint({
  name: 'Anna',
  tMs: onset + 2900,
  kind: 'jitsi-dominant',
});
sealedUnique.recordHint({
  name: 'Boris',
  tMs: onset + 3900,
  kind: 'jitsi-dominant',
});
assert.deepEqual(uniqueCallbacks, ['Boris']);
sealedUnique.recordHint({
  name: 'Charlie',
  tMs: onset + 2900,
  kind: 'jitsi-dominant',
});
sealedUnique.recordHint({
  name: 'Charlie',
  tMs: onset + 2800,
  kind: 'jitsi-dominant',
});
const uniqueAfterContradiction = sealedUnique.resolve(lateA);
assert.equal(uniqueAfterContradiction.source, 'provisional-cluster-id');
assert.deepEqual(uniqueCallbacks, ['Boris', lateA.clusterId]);

// Registering a new past turn behind an asserted seal is a custody
// contradiction. If discovered after lock, it revokes that person exactly once.
const behindSealCallbacks: string[] = [];
const behindSeal = new ClusterNameBinder({
  onLateResolve: (_clusterId, name) => behindSealCallbacks.push(name),
});
behindSeal.recordHint({ name: 'Anna', tMs: onset, kind: 'jitsi-dominant' });
behindSeal.registerAcousticTurn(lateA, true);
behindSeal.sealAcousticThrough(onset + 4001);
behindSeal.recordHint({
  name: 'Anna',
  tMs: onset + 3000,
  kind: 'jitsi-dominant',
  isEnd: true,
});
behindSeal.recordHint({
  name: 'Boris',
  tMs: onset + 3000,
  kind: 'jitsi-dominant',
});
progress(behindSeal, 'Boris', onset + 4000);
const behindSealBeforeContradiction = behindSeal.resolve(lateA);
assert.equal(behindSealBeforeContradiction.speakerName, 'Boris');
assert.deepEqual(behindSealCallbacks, ['Boris']);
behindSeal.registerAcousticTurn(lateB, true);
const behindSealResult = behindSeal.resolve(lateA);
assert.equal(behindSealResult.source, 'provisional-cluster-id');
assert.deepEqual(behindSealCallbacks, ['Boris', lateA.clusterId]);

const causalGreen =
  beforeAdvance.source === 'provisional-cluster-id'
  && afterAdvance.speakerName === 'Boris'
  && afterPostEndHeartbeat.source === 'provisional-cluster-id'
  && afterPostEndTransition.speakerName === 'Boris'
  && overlappingResultA.source === 'provisional-cluster-id'
  && overlappingResultB.source === 'provisional-cluster-id'
  && multiTokenResult.source === 'provisional-cluster-id'
  && acousticCompactedResult.source === 'provisional-cluster-id'
  && transitionCompactedResult.source === 'provisional-cluster-id'
  && swappedResult.speakerName === 'Anna'
  && delayedResult.speakerName === 'Boris'
  && afterOutOfOrderHeartbeat.speakerName === 'Boris'
  && contradictoryResult.source === 'provisional-cluster-id'
  && lateOutgoingAfterTransitionResult.source === 'provisional-cluster-id'
  && beforeMismatchedEnd.source === 'provisional-cluster-id'
  && afterMismatchedEnd.source === 'provisional-cluster-id'
  && resumeResult.source === 'provisional-cluster-id'
  && lateABefore.source === 'provisional-cluster-id'
  && lateAStillUnsealed.source === 'provisional-cluster-id'
  && lateBStillUnsealed.source === 'provisional-cluster-id'
  && lateAAfter.source === 'provisional-cluster-id'
  && lateBAfter.source === 'provisional-cluster-id'
  && uniqueAfterSeal.speakerName === 'Boris'
  && uniqueAfterContradiction.source === 'provisional-cluster-id'
  && behindSealBeforeContradiction.speakerName === 'Boris'
  && behindSealResult.source === 'provisional-cluster-id';

console.log(
  `M1_CAUSAL_${causalGreen ? 'GREEN' : 'RED'} `
  + `platform=jitsi signal=exclusive-trailing onset_to_identity_start_ms=${correctStart - onset} `
  + `before=${beforeAdvance.speakerName}/${beforeAdvance.source} `
  + `after=${afterAdvance.speakerName}/${afterAdvance.source} `
  + `post_end_transition=${afterPostEndTransition.speakerName}/${afterPostEndTransition.source} `
  + `overlap=${overlappingResultA.speakerName},${overlappingResultB.speakerName} `
  + `multi_token=${multiTokenResult.speakerName} `
  + `acoustic_compacted=${acousticCompactedResult.speakerName} `
  + `transition_compacted=${transitionCompactedResult.speakerName} `
  + `swapped=${swappedResult.speakerName} delayed=${delayedResult.speakerName} `
  + `stale_heartbeat=${afterOutOfOrderHeartbeat.speakerName} `
  + `late_distinct=${contradictoryResult.speakerName} `
  + `late_outgoing=${lateOutgoingAfterTransitionResult.speakerName} `
  + `mismatched_end=${beforeMismatchedEnd.speakerName}->${afterMismatchedEnd.speakerName} `
  + `resume=${resumeResult.speakerName} `
  + `late_registration=${lateABefore.speakerName},${lateAAfter.speakerName},${lateBAfter.speakerName} `
  + `sealed_unique=${uniqueBeforeSeal.speakerName}->${uniqueAfterSeal.speakerName}`
  + `->${uniqueAfterContradiction.speakerName} `
  + 'expected_short_turn=unknown->Boris',
);

if (!causalGreen) {
  console.error(
    'Causal violation: exclusive Jitsi testimony named a turn without complete, '
    + 'ordered, globally one-to-one transition custody.',
  );
}
process.exit(causalGreen ? 0 : 1);

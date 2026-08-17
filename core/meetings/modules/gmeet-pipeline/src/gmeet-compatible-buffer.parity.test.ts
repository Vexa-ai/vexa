/**
 * Behavioral parity lock between the untouched Google Meet window manager and its shared copy.
 *
 * This test deliberately imports the Google Meet SOURCE as the oracle. It compares only observable
 * behavior, normalizing wall-clock timestamps that cannot be byte-identical across two sequential
 * runs. Any future Teams adapter must wrap GmeetCompatibleBuffer instead of changing its internals.
 */
import { SpeakerStreamManager as GoogleMeetBuffer } from './speaker-streams.js';
import { GmeetCompatibleBuffer } from '@vexa/transcribe-buffer';

interface Segment { text: string; start: number; end: number }
interface ManagerLike {
  onSegmentReady: ((id: string, name: string, audio: Float32Array) => void) | null;
  onSegmentConfirmed: ((id: string, name: string, text: string, start: number, end: number, segmentId: string, language?: string) => void) | null;
  onSegmentPending: ((id: string, name: string, text: string, start: number, language?: string) => void) | null;
  addSpeaker(id: string, name: string): void;
  feedAudio(id: string, audio: Float32Array, atMs?: number): void;
  handleTranscriptionResult(id: string, text: string, end?: number, segments?: Segment[], language?: string): boolean;
  flushSpeaker(id: string, force?: boolean): Promise<void>;
  getLastConfirmedText(id: string): string;
  removeAll(): void;
}

type Factory = () => ManagerLike;
const source: Factory = () => new GoogleMeetBuffer();
const shared: Factory = () => new GmeetCompatibleBuffer();
const SR = 16_000;
const SID = 'continuous:1';
const speech = (seconds: number) => new Float32Array(Math.round(seconds * SR)).fill(0.1);
const seg = (text: string, start: number, end: number): Segment => ({ text, start, end });
const callPrivate = async (manager: ManagerLike, method: 'trySubmit') =>
  (manager as unknown as Record<string, (id: string) => Promise<void>>)[method](SID);

let checks = 0;
function check(name: string, condition: boolean, detail?: unknown): void {
  if (!condition) throw new Error(`${name}: ${JSON.stringify(detail)}`);
  console.log(`  ✅ ${name}`);
  checks++;
}

function defaults(factory: Factory): Record<string, unknown> {
  const manager = factory() as unknown as Record<string, unknown>;
  const result = {
    minAudioDuration: manager.minAudioDuration,
    submitInterval: manager.submitInterval,
    confirmThreshold: manager.confirmThreshold,
    maxBufferDuration: manager.maxBufferDuration,
    idleTimeoutSec: manager.idleTimeoutSec,
    sampleRate: manager.sampleRate,
    silenceRmsThreshold: manager.silenceRmsThreshold,
  };
  (manager as unknown as ManagerLike).removeAll();
  return result;
}

interface Observation {
  readyLengths: number[];
  pending: string[];
  confirmed: string[];
  accepted: boolean[];
  promptAfterPrefix: string;
}

async function localAgreement(factory: Factory): Promise<Observation> {
  const manager = factory();
  const readyLengths: number[] = [];
  const pending: string[] = [];
  const confirmed: string[] = [];
  const accepted: boolean[] = [];
  manager.onSegmentReady = (_id, _name, audio) => readyLengths.push(audio.length);
  manager.onSegmentPending = (_id, _name, text) => pending.push(text);
  manager.onSegmentConfirmed = (_id, _name, text) => confirmed.push(text);
  manager.addSpeaker(SID, 'Continuous');
  manager.feedAudio(SID, speech(4), 100_000);
  await callPrivate(manager, 'trySubmit');
  accepted.push(manager.handleTranscriptionResult(
    SID,
    'one two three four',
    3,
    [seg('one two', 0, 1), seg('three four', 1, 3)],
    'en',
  ));
  manager.feedAudio(SID, speech(1), 104_000);
  await callPrivate(manager, 'trySubmit');
  accepted.push(manager.handleTranscriptionResult(
    SID,
    'one two three four five',
    4,
    [seg('one two', 0, 1), seg('three four five', 1, 4)],
    'en',
  ));
  const promptAfterPrefix = manager.getLastConfirmedText(SID);
  await callPrivate(manager, 'trySubmit');
  accepted.push(manager.handleTranscriptionResult(SID, 'tail remains stable', 4, undefined, 'en'));
  accepted.push(manager.handleTranscriptionResult(SID, 'tail remains stable', 4, undefined, 'en'));
  manager.removeAll();
  return { readyLengths, pending, confirmed, accepted, promptAfterPrefix };
}

async function fullTextFallback(factory: Factory): Promise<string[]> {
  const manager = factory();
  const confirmed: string[] = [];
  manager.onSegmentConfirmed = (_id, _name, text) => confirmed.push(text);
  manager.addSpeaker(SID, 'Continuous');
  manager.feedAudio(SID, speech(3), 100_000);
  manager.handleTranscriptionResult(SID, 'full text fallback works', 3);
  manager.handleTranscriptionResult(SID, 'full text fallback works', 3);
  manager.removeAll();
  return confirmed;
}

async function finalFlush(factory: Factory): Promise<{ confirmed: string[]; pending: string[] }> {
  const manager = factory();
  const confirmed: string[] = [];
  const pending: string[] = [];
  manager.onSegmentConfirmed = (_id, _name, text) => confirmed.push(text);
  manager.onSegmentPending = (_id, _name, text) => pending.push(text);
  manager.addSpeaker(SID, 'Continuous');
  manager.feedAudio(SID, speech(2), 100_000);
  manager.handleTranscriptionResult(SID, 'last available transcription', 2, [seg('last available transcription', 0, 2)]);
  await manager.flushSpeaker(SID, true);
  manager.removeAll();
  return { confirmed, pending };
}

async function silenceGate(factory: Factory): Promise<number[]> {
  const manager = factory();
  const ready: number[] = [];
  manager.onSegmentReady = (_id, _name, audio) => ready.push(audio.length);
  manager.addSpeaker(SID, 'Continuous');
  manager.feedAudio(SID, new Float32Array(2 * SR), 100_000);
  await callPrivate(manager, 'trySubmit');
  manager.removeAll();
  return ready;
}

async function timestampedInputGap(factory: Factory): Promise<number[]> {
  const manager = factory();
  const ready: number[] = [];
  manager.onSegmentReady = (_id, _name, audio) => ready.push(audio.length);
  manager.addSpeaker(SID, 'Continuous');
  manager.feedAudio(SID, speech(2), 100_000);
  manager.feedAudio(SID, speech(2), 105_000);
  manager.removeAll();
  return ready;
}

async function closeWhileInflight(factory: Factory): Promise<{ ready: number[]; confirmed: string[]; accepted: boolean[] }> {
  const manager = factory();
  const ready: number[] = [];
  const confirmed: string[] = [];
  const accepted: boolean[] = [];
  manager.onSegmentReady = (_id, _name, audio) => ready.push(audio.length);
  manager.onSegmentConfirmed = (_id, _name, text) => confirmed.push(text);
  manager.addSpeaker(SID, 'Continuous');
  manager.feedAudio(SID, speech(2), 100_000);
  await callPrivate(manager, 'trySubmit');
  await manager.flushSpeaker(SID, true);
  accepted.push(manager.handleTranscriptionResult(SID, 'stale draft response', 2, [seg('stale draft response', 0, 2)]));
  accepted.push(manager.handleTranscriptionResult(SID, 'owned final response', 2, [seg('owned final response', 0, 2)]));
  manager.removeAll();
  return { ready, confirmed, accepted };
}

async function hardCap(factory: Factory): Promise<string[]> {
  const manager = factory();
  const confirmed: string[] = [];
  manager.onSegmentConfirmed = (_id, _name, text) => confirmed.push(text);
  manager.addSpeaker(SID, 'Continuous');
  manager.feedAudio(SID, speech(31), 100_000);
  manager.handleTranscriptionResult(SID, 'hard cap last available text', 30, [seg('hard cap last available text', 0, 30)]);
  await callPrivate(manager, 'trySubmit');
  manager.removeAll();
  return confirmed;
}

const expectedDefaults = {
  minAudioDuration: 2,
  submitInterval: 2,
  confirmThreshold: 2,
  maxBufferDuration: 30,
  idleTimeoutSec: 15,
  sampleRate: 16_000,
  silenceRmsThreshold: 0.0025,
};
check('Google Meet defaults are pinned', JSON.stringify(defaults(source)) === JSON.stringify(expectedDefaults), defaults(source));
check('shared defaults equal Google Meet', JSON.stringify(defaults(shared)) === JSON.stringify(defaults(source)), defaults(shared));

for (const [name, scenario] of [
  ['LocalAgreement/common-prefix + offset + prompt', localAgreement],
  ['identical full-text fallback', fullTextFallback],
  ['pending finalization + final flush', finalFlush],
  ['near-silent submission gate', silenceGate],
  ['timestamped batch-input gap guard', timestampedInputGap],
  ['close-while-inflight final resubmit', closeWhileInflight],
  ['30-second hard-cap fallback', hardCap],
] as const) {
  const actual = await scenario(source as never);
  const copied = await scenario(shared as never);
  check(`${name} parity`, JSON.stringify(copied) === JSON.stringify(actual), { actual, copied });
}

const local = await localAgreement(shared);
check('prompt feedback is the last confirmed prefix', local.promptAfterPrefix === 'one two', local);
check('offset lifecycle submits only the remaining four seconds', local.readyLengths.at(-1) === 4 * SR, local.readyLengths);

const teamsVisibleTail = new GmeetCompatibleBuffer({ publishTrailingDraftAfterPrefix: true });
const teamsPending: string[] = [];
const teamsConfirmed: string[] = [];
teamsVisibleTail.onSegmentPending = (_id, _name, text) => teamsPending.push(text);
teamsVisibleTail.onSegmentConfirmed = (_id, _name, text) => teamsConfirmed.push(text);
teamsVisibleTail.addSpeaker(SID, 'Continuous');
teamsVisibleTail.feedAudio(SID, speech(6), 100_000);
teamsVisibleTail.handleTranscriptionResult(
  SID,
  'one two three four',
  4,
  [seg('one two', 0, 1), seg('three four', 1, 4)],
  'en',
);
teamsVisibleTail.handleTranscriptionResult(
  SID,
  'one two three four five',
  5,
  [seg('one two', 0, 1), seg('three four five', 1, 5)],
  'en',
);
check('Teams opt-in keeps the post-prefix tail visible', teamsPending.at(-1) === 'three four five', { teamsPending, teamsConfirmed });
teamsVisibleTail.removeAll();

let replayNow = 200_000;
const replayCadence = new GmeetCompatibleBuffer({
  scheduleSubmissions: false,
  now: () => replayNow,
});
const replayReady: number[] = [];
replayCadence.onSegmentReady = (_id, _name, audio) => replayReady.push(audio.length);
replayCadence.addSpeaker(SID, 'Continuous');
replayCadence.feedAudio(SID, speech(2), replayNow);
check('manual replay does not submit before the GMeet interval',
  await replayCadence.requestTranscription(SID) === false && replayReady.length === 0,
  replayReady);
replayNow += 2_000;
check('manual replay submits when the GMeet interval elapses',
  await replayCadence.requestTranscription(SID) === true && replayReady.length === 1,
  replayReady);
replayCadence.handleTranscriptionResult(SID, 'stable window', 2, [seg('stable window', 0, 2)], 'en');
check('manual replay refuses an immediate repeated call',
  await replayCadence.requestTranscription(SID) === false && replayReady.length === 1,
  replayReady);
replayNow += 1_999;
check('manual replay still refuses just before the next interval',
  await replayCadence.requestTranscription(SID) === false && replayReady.length === 1,
  replayReady);
replayNow += 1;
check('manual replay allows the same window after the minimum stop',
  await replayCadence.requestTranscription(SID) === true && replayReady.length === 2,
  replayReady);
replayCadence.removeAll();

let cachedNow = 300_000;
const cachedReplay = new GmeetCompatibleBuffer({
  scheduleSubmissions: false,
  now: () => cachedNow,
});
let cachedStarts = 0;
cachedReplay.onSegmentReady = (id) => {
  cachedStarts++;
  // A content-addressed fixture-cache hit can complete before requestTranscription resumes.
  cachedReplay.handleTranscriptionResult(id, 'cached stable window', 2, [seg('cached stable window', 0, 2)], 'en');
};
cachedReplay.addSpeaker(SID, 'Continuous');
cachedReplay.feedAudio(SID, speech(2), cachedNow);
cachedNow += 2_000;
check('a synchronously completed cache hit still reports a real submission',
  await cachedReplay.requestTranscription(SID) === true && cachedStarts === 1,
  { cachedStarts });
check('a synchronously completed cache hit still advances the next-call deadline',
  await cachedReplay.requestTranscription(SID) === false && cachedStarts === 1,
  { cachedStarts });
cachedReplay.removeAll();

let laneNow = 400_000;
const routedLane = new GmeetCompatibleBuffer({
  scheduleSubmissions: false,
  flushOnInputGap: false,
  now: () => laneNow,
});
let offTimerStarts = 0;
routedLane.onSegmentReady = () => { offTimerStarts++; };
routedLane.addSpeaker(SID, 'Continuous');
routedLane.feedAudio(SID, speech(2), laneNow);
routedLane.feedAudio(SID, speech(2), laneNow + 5_000);
check('a caller-owned virtual lane can disable the batch-input off-timer trigger',
  offTimerStarts === 0,
  { offTimerStarts });
laneNow += 2_000;
check('the caller-owned lane still submits through the ordinary GMeet interval seam',
  await routedLane.requestTranscription(SID) === true && offTimerStarts === 1,
  { offTimerStarts });
routedLane.removeAll();

console.log(`\n✅ gmeet-compatible-buffer parity: ${checks} checks passed`);

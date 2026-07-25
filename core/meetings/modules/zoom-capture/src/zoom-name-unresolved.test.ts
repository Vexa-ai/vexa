/**
 * C797 Zoom contract: an exclusive active container whose footer cannot resolve a
 * name stays out of the hint stream but must cross one Zoom-specific typed
 * observation after the existing two-poll confirmation envelope.
 *
 * Run:
 *   pnpm --filter @vexa/zoom-capture exec tsx src/zoom-name-unresolved.test.ts
 */
import {
  createZoomSpeakers,
  type ZoomSpeakersOptions,
} from './zoom-speakers.js';

interface ExpectedZoomObservation {
  type: 'name-unresolved';
  platform: 'zoom';
  signal: 'dom-active';
  reason: 'footer-empty' | 'footer-absent' | 'read-fault';
  tMs: number;
}

let intervalCallback: (() => void) | null = null;
const globalObject = globalThis as typeof globalThis & Record<string, unknown>;
globalObject.setInterval = (callback: () => void) => {
  intervalCallback = callback;
  return 1;
};
globalObject.clearInterval = () => {};

type FixtureState = 'none' | 'footer-absent' | 'footer-empty' | 'named' | 'read-fault';
let fixtureState: FixtureState = 'footer-empty';
let fixtureName = '';
const footer = {
  get innerText(): string { return fixtureState === 'named' ? fixtureName : '   '; },
  querySelector: (selector: string) =>
    selector === 'span'
      ? { get textContent(): string { return fixtureState === 'named' ? fixtureName : '   '; } }
      : null,
};
const activeContainer = {
  querySelector: (selector: string) =>
    selector === '.video-avatar__avatar-footer' && fixtureState !== 'footer-absent'
      ? footer
      : null,
};
globalObject.document = {
  visibilityState: 'visible',
  querySelector: (selector: string) => {
    if (fixtureState === 'read-fault') throw new Error('fixture DOM read fault');
    if (fixtureState === 'none') return null;
    return selector === '.speaker-active-container__video-frame' ? activeContainer : null;
  },
  querySelectorAll: () => [],
};

const changes: Array<string | null> = [];
const observations: ExpectedZoomObservation[] = [];
const options = {
  pollMs: 10,
  onSpeakerChange: (name: string | null) => changes.push(name),
  onNameUnresolved: (observation: ExpectedZoomObservation) => observations.push(observation),
} as ZoomSpeakersOptions & {
  onNameUnresolved: (observation: ExpectedZoomObservation) => void;
};

const watcher = createZoomSpeakers(options);
const tick = (count: number): void => {
  for (let index = 0; index < count; index++) intervalCallback?.();
};
tick(1); // constructor tick + one interval tick = two-poll confirmation

if (changes.length !== 0 || watcher.getActiveSpeaker() !== null) {
  throw new Error(`C797_ZOOM_SAFETY_RED: empty identity fabricated a change ${JSON.stringify(changes)}`);
}
if (observations.length !== 1) {
  throw new Error(
    `C797_ZOOM_RED: expected one confirmed footer-empty observation, received ${observations.length}`,
  );
}
const [observation] = observations;
if (
  observation.type !== 'name-unresolved'
  || observation.platform !== 'zoom'
  || observation.signal !== 'dom-active'
  || observation.reason !== 'footer-empty'
  || !Number.isFinite(observation.tMs)
  || observation.tMs < 1_000_000_000_000
) {
  throw new Error(`C797_ZOOM_CONTRACT_RED: malformed observation ${JSON.stringify(observation)}`);
}

tick(50);
if (observations.length !== 1) {
  throw new Error(`C797_ZOOM_CARDINALITY_RED: held empty footer repeated ${observations.length} times`);
}

// Two clean polls clear the diagnostic latch. A one-poll unresolved flicker
// after that is not testimony and emits nothing.
fixtureState = 'none';
tick(2);
fixtureState = 'footer-empty';
tick(1);
fixtureState = 'none';
tick(1);
if (observations.length !== 1) {
  throw new Error(`C797_ZOOM_FLICKER_RED: one-poll unresolved flicker emitted ${observations.length}`);
}

// Footer absence is a distinct, confirmed name-resolution failure.
tick(1); // second clean poll after the flicker
fixtureState = 'footer-absent';
tick(2);
if (observations.length !== 2 || observations[1]?.reason !== 'footer-absent') {
  throw new Error(`C797_ZOOM_REASON_RED: footer absence was not distinct ${JSON.stringify(observations)}`);
}

// A named recovery follows the existing two-poll speaker-change envelope.
fixtureState = 'named';
fixtureName = 'Alpha Example';
tick(2);
if (changes.length !== 1 || changes[0] !== 'Alpha Example') {
  throw new Error(`C797_ZOOM_RECOVERY_RED: named recovery failed ${JSON.stringify(changes)}`);
}

// Two read faults report once and do not clear the previously committed name.
fixtureState = 'read-fault';
tick(2);
if (
  observations.length !== 3
  || observations[2]?.reason !== 'read-fault'
  || watcher.getActiveSpeaker() !== 'Alpha Example'
) {
  throw new Error(
    `C797_ZOOM_FAULT_RED: read fault mutated identity or stayed silent ` +
    `${JSON.stringify({ observations, active: watcher.getActiveSpeaker() })}`,
  );
}
tick(20);
if (observations.length !== 3) {
  throw new Error(`C797_ZOOM_FAULT_CARDINALITY_RED: held read fault repeated ${observations.length} times`);
}

watcher.destroy();
console.log(
  'C797_ZOOM_GREEN: unresolved Zoom identity stays unknown; two-poll reasons, flicker, recovery, and fault cardinality are pinned',
);

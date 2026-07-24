/**
 * C797 Zoom RED: an exclusive active container whose footer cannot resolve a
 * name stays out of the hint stream but must cross one Zoom-specific typed
 * observation after the existing two-poll confirmation envelope.
 *
 * Intentionally excluded from the green package suite until the producer
 * contract exists.
 *
 * Run:
 *   pnpm --filter @vexa/zoom-capture exec tsx src/zoom-name-unresolved.red.test.ts
 */
import {
  createZoomSpeakers,
  type ZoomSpeakersOptions,
} from './zoom-speakers.js';

interface ExpectedZoomObservation {
  type: 'name-unresolved';
  platform: 'zoom';
  signal: 'dom-active';
  reason: 'footer-empty';
  tMs: number;
}

let intervalCallback: (() => void) | null = null;
const globalObject = globalThis as typeof globalThis & Record<string, unknown>;
globalObject.setInterval = (callback: () => void) => {
  intervalCallback = callback;
  return 1;
};
globalObject.clearInterval = () => {};

const emptySpan = { textContent: '   ' };
const emptyFooter = {
  innerText: '   ',
  querySelector: (selector: string) => selector === 'span' ? emptySpan : null,
};
const activeContainer = {
  querySelector: (selector: string) =>
    selector === '.video-avatar__avatar-footer' ? emptyFooter : null,
};
globalObject.document = {
  visibilityState: 'visible',
  querySelector: (selector: string) =>
    selector === '.speaker-active-container__video-frame' ? activeContainer : null,
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
intervalCallback?.(); // constructor tick + one interval tick = two-poll confirmation
watcher.destroy();

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

console.log('C797_ZOOM_GREEN: empty Zoom footer stayed unknown and failed loud after two polls');

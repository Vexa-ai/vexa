/**
 * #797 RED — a real Teams voice-outline transition whose display name cannot
 * be resolved must remain unknown, but it must not disappear silently.
 *
 * This test is intentionally excluded from the package's green suite until
 * the producer exposes the typed observation it requires.
 *
 * Run:
 *   pnpm --filter @vexa/teams-capture exec tsx src/teams-name-unresolved.red.test.ts
 */
import {
  createTeamsSpeakers,
  type TeamsSpeakersOptions,
} from './msteams-speakers.js';

interface NameUnresolvedObservation {
  type: 'name-unresolved';
  platform: 'teams';
  signal: 'dom-outline';
  participantId: string;
  isEnd: boolean;
  tMs: number;
}

const VOICE_OUTLINE = '[data-tid="voice-level-stream-outline"]';
const PARTICIPANT_ID = 'fixture-participant-a';

class FakeElement {
  readonly dataset: Record<string, string> = {};
  readonly isConnected = true;
  parentElement: FakeElement | null = null;

  constructor(
    readonly tagName: string,
    private readonly attrs: Record<string, string> = {},
    private readonly voiceOutline: FakeElement | null = null,
  ) {}

  get classList(): { contains(name: string): boolean } {
    const classes = new Set((this.attrs.class ?? '').split(/\s+/).filter(Boolean));
    return { contains: (name) => classes.has(name) };
  }

  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null;
  }

  querySelector(selector: string): FakeElement | null {
    return selector === VOICE_OUTLINE ? this.voiceOutline : null;
  }

  querySelectorAll(): FakeElement[] {
    return [];
  }

  matches(): boolean {
    return false;
  }
}

const voice = new FakeElement('DIV', {
  'data-tid': 'voice-level-stream-outline',
  class: 'vdi-frame-occlusion',
});
const tile = new FakeElement('DIV', {
  'data-participant-id': PARTICIPANT_ID,
  class: 'fixture-participant-tile',
}, voice);
voice.parentElement = tile;
const body = new FakeElement('BODY');

const globalObject = globalThis as typeof globalThis & Record<string, unknown>;
globalObject.HTMLElement = FakeElement;
globalObject.MutationObserver = class {
  observe(): void {}
  disconnect(): void {}
};
globalObject.requestAnimationFrame = () => 1;
globalObject.cancelAnimationFrame = () => {};
globalObject.document = {
  body,
  querySelector: (selector: string) => selector === '[role="main"]' ? body : null,
  querySelectorAll: (selector: string) =>
    selector === '[data-tid*="participant"]' ? [tile] : [],
};

const hints: Array<{ name: string; id: string; isEnd: boolean }> = [];
const observations: NameUnresolvedObservation[] = [];
const logs: string[] = [];

const options: TeamsSpeakersOptions & {
  onNameUnresolved: (observation: NameUnresolvedObservation) => void;
} = {
  debounceMs: 0,
  log: (message) => logs.push(message),
  onSpeaking: (name, id, isEnd) => hints.push({ name, id, isEnd }),
  onNameUnresolved: (observation) => observations.push(observation),
};

const watcher = createTeamsSpeakers(options);
await new Promise((resolve) => setTimeout(resolve, 10));

const current = {
  speaking: watcher.getSpeaking(),
  hints,
  observations,
  logs,
};
watcher.destroy();
console.log(`C797_CURRENT=${JSON.stringify(current)}`);

if (current.speaking.length !== 1 || current.speaking[0] !== '') {
  throw new Error('C797_HARNESS_RED: the fixture did not reach the unresolved speaking state');
}
if (hints.length !== 0) {
  throw new Error('C797_SAFETY_RED: an unresolved identity crossed as a fabricated hint');
}
if (observations.length !== 1) {
  throw new Error(
    `C797_RED: expected exactly one typed name-unresolved observation, received ${observations.length}`,
  );
}

const [observation] = observations;
if (
  observation.type !== 'name-unresolved'
  || observation.platform !== 'teams'
  || observation.signal !== 'dom-outline'
  || observation.participantId !== PARTICIPANT_ID
  || observation.isEnd
  || !Number.isFinite(observation.tMs)
) {
  throw new Error(`C797_CONTRACT_RED: malformed observation ${JSON.stringify(observation)}`);
}

console.log('C797_GREEN: unresolved Teams identity stayed unknown and emitted one typed observation');

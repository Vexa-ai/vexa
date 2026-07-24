/**
 * A real Teams voice-outline transition whose display name cannot be resolved
 * remains unknown and emits one typed producer observation instead of
 * disappearing silently.
 *
 * Run:
 *   pnpm --filter @vexa/teams-capture exec tsx src/teams-name-unresolved.test.ts
 */
import {
  createTeamsSpeakers,
  type TeamsNameUnresolvedObservation,
  type TeamsSpeakersOptions,
} from './msteams-speakers.js';

const VOICE_OUTLINE = '[data-tid="voice-level-stream-outline"]';
const PARTICIPANT_ID = 'fixture-participant-a';

class FakeElement {
  readonly dataset: Record<string, string> = {};
  readonly isConnected = true;
  parentElement: FakeElement | null = null;
  private nameSurface: FakeElement | null = null;

  constructor(
    readonly tagName: string,
    private readonly attrs: Record<string, string> = {},
    private readonly voiceOutline: FakeElement | null = null,
    readonly textContent = '',
  ) {}

  get classList(): { contains(name: string): boolean } {
    const classes = new Set((this.attrs.class ?? '').split(/\s+/).filter(Boolean));
    return { contains: (name) => classes.has(name) };
  }

  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null;
  }

  setClass(value: string): void {
    this.attrs.class = value;
  }

  setNameSurface(value: FakeElement): void {
    this.nameSurface = value;
  }

  querySelector(selector: string): FakeElement | null {
    if (selector === VOICE_OUTLINE) return this.voiceOutline;
    if (selector === 'span[title]') return this.nameSurface;
    return null;
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
const silentVoice = new FakeElement('DIV', {
  'data-tid': 'voice-level-stream-outline',
});
const silentTile = new FakeElement('DIV', {
  'data-participant-id': 'fixture-silent-unresolved',
  class: 'fixture-participant-tile',
}, silentVoice);
silentVoice.parentElement = silentTile;
const body = new FakeElement('BODY');
const rafCallbacks: Array<() => void> = [];

const globalObject = globalThis as typeof globalThis & Record<string, unknown>;
globalObject.HTMLElement = FakeElement;
globalObject.MutationObserver = class {
  observe(): void {}
  disconnect(): void {}
};
globalObject.requestAnimationFrame = (callback: () => void) => {
  rafCallbacks.push(callback);
  return rafCallbacks.length;
};
globalObject.cancelAnimationFrame = () => {};
globalObject.document = {
  body,
  querySelector: (selector: string) => selector === '[role="main"]' ? body : null,
  querySelectorAll: (selector: string) =>
    selector === '[data-tid*="participant"]' ? [tile, silentTile] : [],
};

const hints: Array<{ name: string; id: string; isEnd: boolean }> = [];
const observations: TeamsNameUnresolvedObservation[] = [];
const logs: string[] = [];

const options: TeamsSpeakersOptions & {
  onNameUnresolved: (observation: TeamsNameUnresolvedObservation) => void;
} = {
  debounceMs: 0,
  heartbeatMs: 20,
  log: (message) => logs.push(message),
  onSpeaking: (name, id, isEnd) => hints.push({ name, id, isEnd }),
  onNameUnresolved: (observation) => observations.push(observation),
};

const watcher = createTeamsSpeakers(options);
await new Promise((resolve) => setTimeout(resolve, 70));

const current = {
  speaking: watcher.getSpeaking(),
  hints,
  observations,
  logs,
};
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
if (observations.some((item) => item.edge === 'end')) {
  throw new Error('C797_CARDINALITY_RED: a bootstrap-silent tile emitted a false unresolved END');
}

const [observation] = observations;
if (
  observation.type !== 'name-unresolved'
  || observation.platform !== 'teams'
  || observation.signal !== 'dom-outline'
  || observation.reason !== 'resolver-empty'
  || observation.edge !== 'start'
  || !Number.isFinite(observation.tMs)
  || observation.tMs < 1_000_000_000_000
) {
  throw new Error(`C797_CONTRACT_RED: malformed observation ${JSON.stringify(observation)}`);
}

// A held unresolved outline remains one episode: its 20ms test heartbeat does
// not manufacture repeated observations. A real end edge closes it exactly once.
await new Promise((resolve) => setTimeout(resolve, 220));
voice.setClass('');
const pendingRaf = rafCallbacks.splice(0);
for (const callback of pendingRaf) callback();
await new Promise((resolve) => setTimeout(resolve, 20));
if (observations.length !== 2 || observations[1]?.edge !== 'end') {
  throw new Error(`C797_CARDINALITY_RED: expected one start and one end, got ${JSON.stringify(observations)}`);
}

// A later-rendered name is recovered by the real Teams heartbeat and becomes a
// normal named start. The unresolved episode is not repeated or promoted.
voice.setClass('vdi-frame-occlusion');
tile.setNameSurface(new FakeElement('SPAN', { title: 'Alpha Example' }, null, 'Alpha Example'));
await new Promise((resolve) => setTimeout(resolve, 240));
const nextRaf = rafCallbacks.splice(0);
for (const callback of nextRaf) callback();
await new Promise((resolve) => setTimeout(resolve, 60));
if (!hints.some((hint) => hint.name === 'Alpha Example' && hint.isEnd === false)) {
  throw new Error(`C797_REPAIR_RED: late-rendered name did not recover on heartbeat: ${JSON.stringify(hints)}`);
}
if (observations.length !== 2) {
  throw new Error(`C797_HEARTBEAT_RED: heartbeat duplicated unresolved observations: ${JSON.stringify(observations)}`);
}

watcher.destroy();
console.log('C797_GREEN: unresolved Teams identity stayed unknown; bootstrap, edges, heartbeat, and late-name repair are pinned');

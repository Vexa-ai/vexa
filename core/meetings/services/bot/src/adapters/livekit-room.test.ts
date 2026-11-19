/**
 * The LiveKit adapter maps the SDK's room onto `CallRoom` (#1783): joins, leaves, audio frames of
 * microphone tracks only, participants already present, and a disconnect — and it subscribes only to
 * what it reads. Offline: a fake SDK.
 *
 * Run: tsx src/adapters/livekit-room.test.ts
 */
import { createLiveKitRoom, type LiveKitSdk, type LkParticipant, type LkPublication } from './livekit-room.js';
import type { CallAudioFrame } from '../call.js';

let passed = 0, failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  if (cond) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
};

const AUDIO = 1, VIDEO = 2;
let lastRoom: any = null;
/** A publication that records what the listener asked of it. */
const pub = (kind: number, source: number): LkPublication & { asked: boolean[] } => {
  const asked: boolean[] = [];
  return { kind, source, asked, setSubscribed: (v: boolean) => { asked.push(v); } };
};
const adaMic = pub(AUDIO, 2), adaCamera = pub(VIDEO, 1), adaScreenAudio = pub(AUDIO, 4), adaScreen = pub(VIDEO, 3);
const sdk: LiveKitSdk = {
  Room: class {
    remoteParticipants = new Map<string, LkParticipant>([['u:7', { identity: 'u:7', name: 'Ada',
      trackPublications: new Map<string, LkPublication>([['m', adaMic], ['c', adaCamera], ['sa', adaScreenAudio], ['s', adaScreen]]) }]]);
    handlers = new Map<string, (...a: any[]) => void>();
    connected: { url: string; token: string; opts: unknown } | null = null;
    disconnected = 0;
    constructor() { lastRoom = this; }
    on(e: string, cb: (...a: any[]) => void) { this.handlers.set(e, cb); return this; }
    async connect(url: string, token: string, opts: unknown) { this.connected = { url, token, opts }; }
    async disconnect() { this.disconnected++; }
  } as never,
  AudioStream: class {
    constructor(public track: { frames: CallAudioFrame[] }, public o: unknown) {}
    async *[Symbol.asyncIterator]() { for (const f of this.track.frames) yield f; }
  } as never,
  RoomEvent: { ParticipantConnected: 'pc', ParticipantDisconnected: 'pd', TrackPublished: 'tp', TrackSubscribed: 'ts', Disconnected: 'd' },
  TrackKind: { KIND_AUDIO: AUDIO },
};

const events: string[] = [];
const room = createLiveKitRoom({ sdk, url: 'ws://livekit:7880', token: 'tok' });
await room.connect({
  participantJoined: (p) => events.push(`join ${p.identity} ${p.name}`),
  participantLeft: (p) => events.push(`left ${p.identity} ${p.left}`),
  audio: (p, f) => events.push(`audio ${p.identity} ${p.source ?? 'mic'} ${f.data.length}`),
  disconnected: (r) => events.push(`gone ${r}`),
});
check('connects with the listener token, subscribing to nothing on its own',
  lastRoom.connected.url === 'ws://livekit:7880' && lastRoom.connected.token === 'tok'
  && (lastRoom.connected.opts as { autoSubscribe: boolean }).autoSubscribe === false);
check('a participant already there: their microphone and shared-screen audio are subscribed, no video',
  adaMic.asked.join() === 'true' && adaScreenAudio.asked.join() === 'true'
  && adaCamera.asked.length === 0 && adaScreen.asked.length === 0,
  [adaMic, adaCamera, adaScreenAudio, adaScreen].map((x) => x.asked.join()).join('|'));
const lateMic = pub(AUDIO, 2), lateCamera = pub(VIDEO, 1);
lastRoom.handlers.get('tp')(lateMic, { identity: 'u:8', name: 'Grace' });
lastRoom.handlers.get('tp')(lateCamera, { identity: 'u:8', name: 'Grace' });
check('a track published later is subscribed only when it is read',
  lateMic.asked.join() === 'true' && lateCamera.asked.length === 0);
check('a participant already there is a join', events[0] === 'join u:7 Ada', events.join('|'));
lastRoom.handlers.get('pc')({ identity: 'u:8', name: 'Grace' });
const frame = { data: new Int16Array(160), sampleRate: 16000, channels: 1 };
lastRoom.handlers.get('ts')({ kind: AUDIO, frames: [frame, frame] }, {}, { identity: 'u:8', name: 'Grace' });
lastRoom.handlers.get('ts')({ kind: VIDEO, frames: [frame] }, {}, { identity: 'u:8', name: 'Grace' });
await new Promise((r) => setTimeout(r, 10));
check('microphone frames reach the handler, camera tracks are not read',
  events.filter((e) => e.startsWith('audio u:8 mic 160')).length === 2, events.join('|'));
// A shared screen with sound: its audio track is its own source, told apart from the microphone.
lastRoom.handlers.get('ts')({ kind: AUDIO, frames: [frame] }, { source: 4 }, { identity: 'u:8', name: 'Grace' });
lastRoom.handlers.get('ts')({ kind: AUDIO, frames: [frame] }, { source: 2 }, { identity: 'u:7', name: 'Ada' });
lastRoom.handlers.get('ts')({ kind: VIDEO, frames: [frame] }, { source: 3 }, { identity: 'u:8', name: 'Grace' });
await new Promise((r) => setTimeout(r, 10));
check('shared-screen audio is read and marked as the shared screen',
  events.filter((e) => e === 'audio u:8 screen_share_audio 160').length === 1, events.join('|'));
check('a microphone publication is read as the person\'s voice',
  events.filter((e) => e === 'audio u:7 mic 160').length === 1, events.join('|'));
check('screen video is never read', events.filter((e) => e.startsWith('audio') ).length === 4, events.join('|'));
lastRoom.handlers.get('pd')({ identity: 'u:8', disconnectReason: 1 });
lastRoom.handlers.get('pc')({ identity: 'u:9', name: 'Lin' });
lastRoom.handlers.get('pd')({ identity: 'u:9', disconnectReason: 0 });
lastRoom.handlers.get('d')('ROOM_DELETED');
check('leaves and the room going away are reported', events.includes('left u:8 leave') && events.includes('gone ROOM_DELETED'), events.join('|'));
check('a Leave (client-initiated) is told apart from a lost connection', events.includes('left u:9 lost'), events.join('|'));
await room.disconnect();
check('disconnect leaves the room', lastRoom.disconnected === 1);

// The policy switch: shared-screen audio off ⇒ that track is never subscribed nor read; microphones still are.
for (const x of [adaMic, adaCamera, adaScreenAudio, adaScreen]) x.asked.length = 0;
const offEvents: string[] = [];
const offRoom = createLiveKitRoom({ sdk, url: 'ws://livekit:7880', token: 'tok', screenShareAudio: false });
await offRoom.connect({
  participantJoined: () => {}, participantLeft: () => {}, disconnected: () => {},
  audio: (p, f) => offEvents.push(`audio ${p.identity} ${p.source ?? 'mic'} ${f.data.length}`),
});
lastRoom.handlers.get('ts')({ kind: AUDIO, frames: [frame] }, { source: 4 }, { identity: 'u:8', name: 'Grace' });
lastRoom.handlers.get('ts')({ kind: AUDIO, frames: [frame] }, { source: 2 }, { identity: 'u:8', name: 'Grace' });
await new Promise((r) => setTimeout(r, 10));
check('policy off: shared-screen audio is not read, the microphone is',
  offEvents.length === 1 && offEvents[0] === 'audio u:8 mic 160', offEvents.join('|'));
check('policy off: a shared screen\'s audio already there is never subscribed, the microphone is',
  adaScreenAudio.asked.length === 0 && adaMic.asked.join() === 'true',
  [adaMic, adaScreenAudio].map((x) => x.asked.join()).join('|'));
const lateScreenAudio = pub(AUDIO, 4);
lastRoom.handlers.get('tp')(lateScreenAudio, { identity: 'u:8', name: 'Grace' });
check('policy off: a shared screen\'s audio published later is never subscribed', lateScreenAudio.asked.length === 0);
const pushed = pub(AUDIO, 4);
lastRoom.handlers.get('ts')({ kind: AUDIO, frames: [frame] }, pushed, { identity: 'u:8', name: 'Grace' });
await new Promise((r) => setTimeout(r, 10));
check('policy off: a shared screen\'s audio subscribed anyway is dropped at once and not read',
  pushed.asked.join() === 'false' && offEvents.length === 1, pushed.asked.join());
await offRoom.disconnect();

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exit(1);

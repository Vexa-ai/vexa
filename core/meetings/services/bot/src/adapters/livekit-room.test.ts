/**
 * The LiveKit adapter maps the SDK's room onto `CallRoom` (#1783): joins, leaves, audio frames of
 * microphone tracks only, participants already present, and a disconnect. Offline: a fake SDK.
 *
 * Run: tsx src/adapters/livekit-room.test.ts
 */
import { createLiveKitRoom, type LiveKitSdk, type LkParticipant } from './livekit-room.js';
import type { CallAudioFrame } from '../call.js';

let passed = 0, failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  if (cond) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
};

const AUDIO = 1, VIDEO = 2;
let lastRoom: any = null;
const sdk: LiveKitSdk = {
  Room: class {
    remoteParticipants = new Map<string, LkParticipant>([['u:7', { identity: 'u:7', name: 'Ada' }]]);
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
  RoomEvent: { ParticipantConnected: 'pc', ParticipantDisconnected: 'pd', TrackSubscribed: 'ts', Disconnected: 'd' },
  TrackKind: { KIND_AUDIO: AUDIO },
};

const events: string[] = [];
const room = createLiveKitRoom({ sdk, url: 'ws://livekit:7880', token: 'tok' });
await room.connect({
  participantJoined: (p) => events.push(`join ${p.identity} ${p.name}`),
  participantLeft: (p) => events.push(`left ${p.identity} ${p.left}`),
  audio: (p, f) => events.push(`audio ${p.identity} ${f.data.length}`),
  disconnected: (r) => events.push(`gone ${r}`),
});
check('connects with the listener token, subscribing automatically',
  lastRoom.connected.url === 'ws://livekit:7880' && lastRoom.connected.token === 'tok'
  && (lastRoom.connected.opts as { autoSubscribe: boolean }).autoSubscribe === true);
check('a participant already there is a join', events[0] === 'join u:7 Ada', events.join('|'));
lastRoom.handlers.get('pc')({ identity: 'u:8', name: 'Grace' });
const frame = { data: new Int16Array(160), sampleRate: 16000, channels: 1 };
lastRoom.handlers.get('ts')({ kind: AUDIO, frames: [frame, frame] }, {}, { identity: 'u:8', name: 'Grace' });
lastRoom.handlers.get('ts')({ kind: VIDEO, frames: [frame] }, {}, { identity: 'u:8', name: 'Grace' });
await new Promise((r) => setTimeout(r, 10));
check('microphone frames reach the handler, camera tracks are not read',
  events.filter((e) => e.startsWith('audio u:8 160')).length === 2, events.join('|'));
lastRoom.handlers.get('pd')({ identity: 'u:8', disconnectReason: 1 });
lastRoom.handlers.get('pc')({ identity: 'u:9', name: 'Lin' });
lastRoom.handlers.get('pd')({ identity: 'u:9', disconnectReason: 0 });
lastRoom.handlers.get('d')('ROOM_DELETED');
check('leaves and the room going away are reported', events.includes('left u:8 leave') && events.includes('gone ROOM_DELETED'), events.join('|'));
check('a Leave (client-initiated) is told apart from a lost connection', events.includes('left u:9 lost'), events.join('|'));
await room.disconnect();
check('disconnect leaves the room', lastRoom.disconnected === 1);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exit(1);

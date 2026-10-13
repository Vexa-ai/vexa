/**
 * virtual-camera L2 — the four routes a video track reaches a meeting by, against an in-memory
 * DOM/WebRTC shim. Pins: the page never opens a real camera, a platform cannot replace the bot's
 * video with its own (or with nothing), an offer always carries a send-capable video transceiver,
 * the canvas track is pushed onto live senders after a renegotiation, and a still image keeps
 * producing frames. The page behaviour is validated in a live meeting; this pins the logic over it.
 * Run: npx tsx src/virtual-camera.test.ts
 */
import {
  applyVirtualCameraTrack,
  virtualCameraFramesSent,
  clearVirtualCamera,
  drawVirtualCameraImage,
  installVirtualCamera,
  startVirtualCameraPump,
  stopVirtualCameraPump,
  virtualCameraState,
} from './virtual-camera.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ── shim: canvas + tracks + streams ────────────────────────────────────────────────────────────────
let trackSeq = 0;
class FakeTrack {
  id = `track-${++trackSeq}`;
  readyState = 'live';
  constructor(public kind: string, public label = '') {}
  clone(): FakeTrack { const t = new FakeTrack(this.kind, this.label); return t; }
  stop(): void { this.readyState = 'ended'; }
}
class FakeStream {
  tracks: FakeTrack[] = [];
  constructor(tracks: FakeTrack[] = []) { this.tracks = tracks; }
  addTrack(t: FakeTrack): void { this.tracks.push(t); }
  getVideoTracks(): FakeTrack[] { return this.tracks.filter((t) => t.kind === 'video'); }
  getAudioTracks(): FakeTrack[] { return this.tracks.filter((t) => t.kind === 'audio'); }
  getTracks(): FakeTrack[] { return this.tracks; }
}
const drawn: Array<{ w: number; h: number }> = [];
let pixelWrites = 0;
class FakeCanvas {
  width = 0; height = 0;
  fills: string[] = [];
  captured = 0;
  getContext(): unknown {
    return {
      fillStyle: '#000',
      imageSmoothingEnabled: false,
      fillRect: (_x: number, _y: number, w: number, h: number) => { this.fills.push(`${w}x${h}`); },
      drawImage: (_img: unknown, _x: number, _y: number, w: number, h: number) => { drawn.push({ w, h }); },
      getImageData: () => ({ data: [0, 0, 0, 255] }),
      putImageData: () => { pixelWrites++; },
    };
  }
  captureStream(): FakeStream { this.captured++; return new FakeStream([new FakeTrack('video', 'canvas')]); }
}
let lastCanvas: FakeCanvas | null = null;
(globalThis as any).document = { createElement: (tag: string) => { if (tag !== 'canvas') return {}; lastCanvas = new FakeCanvas(); return lastCanvas; } };
(globalThis as any).MediaStream = FakeStream;
let imageLoads = true;
(globalThis as any).Image = class {
  width = 400; height = 200;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  set src(_v: string) { setTimeout(() => (imageLoads ? this.onload?.() : this.onerror?.()), 0); }
};

// ── shim: media devices ────────────────────────────────────────────────────────────────────────────
const gumCalls: unknown[] = [];
let devices: Array<{ kind: string; deviceId: string }> = [{ kind: 'audioinput', deviceId: 'mic' }];
// node defines `navigator` as a getter, so the shim is installed rather than assigned.
Object.defineProperty(globalThis, 'navigator', { configurable: true, writable: true, value: {
  mediaDevices: {
    async getUserMedia(constraints: any) {
      gumCalls.push(constraints);
      const s = new FakeStream();
      if (constraints?.video) s.addTrack(new FakeTrack('video', 'real camera'));
      if (constraints?.audio) s.addTrack(new FakeTrack('audio', 'real mic'));
      return s;
    },
    async enumerateDevices() { return devices.slice(); },
  },
} });

// ── shim: WebRTC ───────────────────────────────────────────────────────────────────────────────────
class FakeSender {
  constructor(public track: FakeTrack | null) {}
  async replaceTrack(t: FakeTrack | null): Promise<void> { this.track = t; }
}
class FakeTransceiver {
  constructor(public sender: FakeSender, public direction: string, public receiver: { track: FakeTrack | null }) {}
}
class FakeRTCPeerConnection {
  static staticFlag = 'kept';
  connectionState = 'connected';
  transceivers: FakeTransceiver[] = [];
  senders: FakeSender[] = [];
  listeners: Record<string, Array<() => void>> = {};
  offers = 0;
  addTrack(track: FakeTrack): FakeSender { const s = new FakeSender(track); this.senders.push(s); return s; }
  addTransceiver(track: FakeTrack, init: { direction: string }): FakeTransceiver {
    const s = new FakeSender(track);
    this.senders.push(s);
    const t = new FakeTransceiver(s, init.direction, { track: null });
    this.transceivers.push(t);
    return t;
  }
  getTransceivers(): FakeTransceiver[] { return this.transceivers; }
  getSenders(): FakeSender[] { return this.senders; }
  async createOffer(): Promise<{ type: string }> { this.offers++; return { type: 'offer' }; }
  stats: Array<Record<string, unknown>> = [];
  async getStats(): Promise<{ forEach: (fn: (r: unknown) => void) => void }> {
    if (this.connectionState === 'broken') throw new Error('connection gone');
    const rows = this.stats;
    return { forEach: (fn) => rows.forEach(fn) };
  }
  addEventListener(name: string, fn: () => void): void { (this.listeners[name] ||= []).push(fn); }
  emit(name: string): void { (this.listeners[name] || []).forEach((f) => f()); }
}
(globalThis as any).RTCPeerConnection = FakeRTCPeerConnection;
(globalThis as any).RTCRtpSender = FakeSender;

async function main() {
  const logs: string[] = [];
  check('install reports success', installVirtualCamera({ log: (m) => logs.push(m), width: 640, height: 360, fps: 20 }));
  check('install is idempotent (a second navigation adds no second canvas)', !installVirtualCamera({}));
  const state = virtualCameraState();
  check('state reports the installed canvas', state.installed && state.width === 640 && state.height === 360 && state.videoTracks === 1,
    JSON.stringify(state));

  // ── getUserMedia: the page never gets a real camera ──
  const videoOnly = await (navigator as any).mediaDevices.getUserMedia({ video: true });
  check('a video request is answered with the canvas, not a camera',
    videoOnly.getVideoTracks().length === 1 && videoOnly.getVideoTracks()[0].label === 'canvas', JSON.stringify(gumCalls));
  check('the camera itself is never opened', !gumCalls.some((c: any) => c?.video));

  const both = await (navigator as any).mediaDevices.getUserMedia({ video: true, audio: true });
  check('video+audio returns the canvas video and the real microphone',
    both.getVideoTracks()[0].label === 'canvas' && both.getAudioTracks()[0].label === 'real mic');

  const audioOnly = await (navigator as any).mediaDevices.getUserMedia({ audio: true });
  check('an audio-only request is passed straight through', audioOnly.getAudioTracks()[0].label === 'real mic' && audioOnly.getVideoTracks().length === 0);

  // ── enumerateDevices: a camera is advertised when the machine reports none ──
  const listed = await (navigator as any).mediaDevices.enumerateDevices();
  check('a videoinput is advertised when the machine has none', listed.filter((d: any) => d.kind === 'videoinput').length === 1);
  devices = [{ kind: 'audioinput', deviceId: 'mic' }, { kind: 'videoinput', deviceId: 'real-cam' }];
  const listed2 = await (navigator as any).mediaDevices.enumerateDevices();
  check('a real camera is not duplicated', listed2.filter((d: any) => d.kind === 'videoinput').length === 1);

  // ── addTrack / replaceTrack ──
  const pc = new (globalThis as any).RTCPeerConnection();
  check('every peer connection is registered', virtualCameraState().peerConnections === 1);
  const platformTrack = new FakeTrack('video', 'platform camera');
  const sender = pc.addTrack(platformTrack);
  check("addTrack swaps the platform's camera for the canvas", sender.track.label === 'canvas', sender.track.label);
  const audioSender = pc.addTrack(new FakeTrack('audio', 'mic'));
  pc.senders.push(new FakeSender(null));   // an audio transceiver whose track is not attached yet
  check('audio tracks are left alone', audioSender.track.label === 'mic');

  await sender.replaceTrack(new FakeTrack('video', 'platform camera again'));
  check('replaceTrack substitutes the canvas', sender.track.label === 'canvas', sender.track.label);
  await sender.replaceTrack(null);
  check('clearing the track (camera toggle) cannot leave the sender empty', sender.track !== null && sender.track.label === 'canvas');

  // ── createOffer: video is send-capable before the offer goes out ──
  const idle = new (globalThis as any).RTCPeerConnection();
  const idleSender = new FakeSender(null);
  idle.transceivers.push(new FakeTransceiver(idleSender, 'recvonly', { track: new FakeTrack('video', 'remote') }));
  idle.senders.push(idleSender);
  await idle.createOffer();
  check('an inactive video transceiver is turned send-capable', idle.transceivers[0].direction === 'sendrecv');
  check('and carries the canvas before the offer', idleSender.track?.label === 'canvas');

  const empty = new (globalThis as any).RTCPeerConnection();
  await empty.createOffer();
  check('a connection with no video transceiver gets one', empty.transceivers.length === 1 && empty.transceivers[0].sender.track.label === 'canvas');
  check('offers are counted for diagnostics', virtualCameraState().intercepted.offers === 1);

  // ── shapeOffers: false — the platform's offers are left exactly as it built them ──
  const vcGlobals = (globalThis as any).__vexaVirtualCamera;
  vcGlobals.shapeOffers = false;
  const plain = new (globalThis as any).RTCPeerConnection();
  await plain.createOffer();
  const recvOnly = new (globalThis as any).RTCPeerConnection();
  const recvSender = new FakeSender(null);
  recvOnly.transceivers.push(new FakeTransceiver(recvSender, 'recvonly', { track: new FakeTrack('video', 'remote') }));
  recvOnly.senders.push(recvSender);
  await recvOnly.createOffer();
  check('without offer shaping no video section is added', plain.transceivers.length === 0 && virtualCameraState().intercepted.offers === 1);
  check('nor a direction changed', recvOnly.transceivers[0].direction === 'recvonly' && recvSender.track === null);
  vcGlobals.shapeOffers = true;
  plain.connectionState = 'closed';
  recvOnly.connectionState = 'closed';

  // ── applyVirtualCameraTrack: after a renegotiation ──
  const before = lastCanvas!.captured;
  const applied = await applyVirtualCameraTrack();
  check('a fresh canvas track is pushed onto live senders', applied.replaced >= 1 && applied.errors.length === 0, JSON.stringify(applied));
  check('the track is fresh (captureStream called again)', lastCanvas!.captured > before);
  check('an audio sender is never pushed a video track', audioSender.track?.label === 'mic', String(audioSender.track?.label));
  check('a sender with no track and no incoming video is left alone (the offer hook owns that case)',
    applied.senders === applied.replaced, JSON.stringify(applied));

  pc.connectionState = 'closed';
  const afterClose = await applyVirtualCameraTrack();
  check('a closed connection is skipped', afterClose.senders < applied.senders, JSON.stringify(afterClose));

  const registered = virtualCameraState().peerConnections;
  const dead = new (globalThis as any).RTCPeerConnection();
  dead.connectionState = 'failed';
  dead.emit('connectionstatechange');
  check('a failed connection is forgotten (the registry does not grow with dead connections)',
    virtualCameraState().peerConnections === registered, `${virtualCameraState().peerConnections} vs ${registered}`);

  // ── is the meeting actually sending our video? ──
  check('no frames yet reads as zero', (await virtualCameraFramesSent()) === 0);
  idle.stats = [{ type: 'outbound-rtp', kind: 'video', framesSent: 120 }, { type: 'inbound-rtp', kind: 'video', framesSent: 999 }];
  empty.stats = [{ type: 'outbound-rtp', kind: 'video', framesSent: 30 }];
  check('outbound video frames are summed across connections, inbound ignored', (await virtualCameraFramesSent()) === 150,
    String(await virtualCameraFramesSent()));
  const broken = new (globalThis as any).RTCPeerConnection();
  broken.connectionState = 'broken';
  check('a connection whose stats cannot be read does not break the probe', (await virtualCameraFramesSent()) === 150);

  // ── what the camera shows ──
  drawn.length = 0;
  check('an image is drawn contain-fit on black', (await drawVirtualCameraImage('data:image/png;base64,AAAA')) && drawn.length === 1
    && drawn[0].w === 640 && drawn[0].h === 320, JSON.stringify(drawn));
  imageLoads = false;
  check('an image that cannot be loaded reports false', !(await drawVirtualCameraImage('https://example.test/missing.png')));
  imageLoads = true;
  check('clear paints black', clearVirtualCamera());

  // ── the pump keeps a still image flowing ──
  check('the pump starts once', startVirtualCameraPump() && !startVirtualCameraPump());
  const pumpedBefore = pixelWrites;
  await sleep(120);
  check('frames keep being produced for a still image', pixelWrites > pumpedBefore, `${pixelWrites} vs ${pumpedBefore}`);
  check('state reports the pump', virtualCameraState().pumping);
  check('the pump stops', stopVirtualCameraPump() && !stopVirtualCameraPump());
  const pumpedAfter = pixelWrites;
  await sleep(60);
  check('and stays stopped', pixelWrites === pumpedAfter);

  check('static properties of RTCPeerConnection survive the hook', (globalThis as any).RTCPeerConnection.staticFlag === 'kept');

  if (failed) { console.error(`\n❌ virtual-camera: ${failed} check(s) failed`); process.exit(1); }
  console.log('\n✅ virtual-camera: all checks passed');
}

main();

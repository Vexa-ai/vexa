/**
 * webrtc-dedup — one remote track must yield ONE mirrored stream.
 *
 * The hook patches RTCPeerConnection twice over: it calls `addEventListener('track', handleTrack)`
 * AND wraps the `ontrack` setter. Both fire for the same RTCTrackEvent, so before the dedup every
 * remote track was mirrored twice — two <audio> elements and, more importantly, the same audio
 * pushed twice into `__vexaCapturedRemoteAudioStreams`. The mixed lane then summed both copies
 * into one AudioContext destination, feeding every word to the transcriber twice.
 *
 * Pure DOM/WebRTC stubs — no browser, no meeting. What is asserted is the mirroring bookkeeping,
 * which is where the doubling entered.
 *
 * Run: npx tsx src/webrtc-dedup.test.ts
 */
let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

// ── Minimal browser surface the hook touches. ──
const el = () => ({ dataset: {} as Record<string, string>, style: {} as Record<string, string>,
  srcObject: null as unknown, autoplay: false, muted: false, volume: 0, play: () => Promise.resolve() });
const win: any = globalThis as any;
win.window = win;
win.document = { body: { appendChild: () => {} }, createElement: () => el(), addEventListener: () => {} };
win.MediaStream = class { tracks: any[]; constructor(t: any[] = []) { this.tracks = t; } getAudioTracks() { return this.tracks; } };

/** A peer connection that, like the real patched one, delivers each track event down BOTH paths. */
class FakePC {
  private listeners: ((e: any) => void)[] = [];
  private _ontrack: ((e: any) => void) | null = null;
  addEventListener(type: string, fn: (e: any) => void): void { if (type === 'track') this.listeners.push(fn); }
  /** Fire ONE track event the way the browser does — every listener AND the ontrack handler. */
  emit(event: any): void {
    for (const fn of this.listeners) fn(event);
    (this as any).ontrack?.(event);
  }
  close(): void { /* unused */ }
}
// `ontrack` must be a PROTOTYPE ACCESSOR, as it is in a real RTCPeerConnection: the hook only
// wraps the setter when it finds one on the prototype (getOwnPropertyDescriptor(...).set). Declared
// as a plain instance field, the wrapper silently never installs — the second mirroring path is
// absent and the very bug under test cannot occur, so the test would pass against broken code.
Object.defineProperty(FakePC.prototype, 'ontrack', {
  get(this: any) { return this._ontrack; },
  set(this: any, fn: any) { this._ontrack = fn; },
  configurable: true, enumerable: true,
});
win.RTCPeerConnection = FakePC as any;

const { installRemoteAudioHook } = await import('./webrtc-audio-hook.js');

check('hook installs over the stubbed RTCPeerConnection', installRemoteAudioHook({ log: () => {} }) === true);

// The hook patched the constructor; build a connection through the patched global.
const pc: any = new (globalThis as any).RTCPeerConnection();
// THE DOUBLE PATH. The hook registers handleTrack via addEventListener AND wraps the `ontrack`
// SETTER — but the wrapper is only installed once the page assigns ontrack, which the Teams app
// does. Assigning it here is what makes this stub reproduce the real double-mirror; without this
// line only one path is live and the bug is invisible.
pc.ontrack = () => { /* the meeting app's own handler */ };
const track = { id: 'mainAudio-abc123', kind: 'audio' };
const stream = new win.MediaStream([track]);
pc.emit({ track, streams: [stream] });

check('ONE track event yields ONE mirrored stream (not two)',
  win.__vexaCapturedRemoteAudioStreams.length === 1, `n=${win.__vexaCapturedRemoteAudioStreams.length}`);
check('ONE track event yields ONE injected <audio> element',
  win.__vexaInjectedAudioElements.length === 1, `n=${win.__vexaInjectedAudioElements.length}`);

// A REPEAT of the same track id (a renegotiation re-firing the same track) must not mirror again.
pc.emit({ track, streams: [stream] });
check('a re-fired event for the same track id does not mirror again',
  win.__vexaCapturedRemoteAudioStreams.length === 1, `n=${win.__vexaCapturedRemoteAudioStreams.length}`);

// A genuinely DIFFERENT track still gets mirrored — the dedup keys on identity, not on count.
const track2 = { id: 'dominantSpeaker-9', kind: 'audio' };
pc.emit({ track: track2, streams: [new win.MediaStream([track2])] });
check('a different track is still mirrored (dedup is by id, not a cap)',
  win.__vexaCapturedRemoteAudioStreams.length === 2, `n=${win.__vexaCapturedRemoteAudioStreams.length}`);

// Video is not our business.
pc.emit({ track: { id: 'video-1', kind: 'video' }, streams: [] });
check('video tracks are ignored', win.__vexaCapturedRemoteAudioStreams.length === 2,
  `n=${win.__vexaCapturedRemoteAudioStreams.length}`);

if (failed) { console.error(`\n❌ webrtc-dedup: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ webrtc-dedup: each remote audio track is mirrored exactly once, so the mixer cannot sum a track with itself.');

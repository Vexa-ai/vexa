/**
 * jitsi-placeholder — a Jitsi SDP placeholder is not a participant.
 *
 * Jitsi signals a permanent dummy audio m-line (MediaStream id `mixedmslabel`, track
 * `mixedlabelaudio0`; `default` on older builds) so the SDP shape survives renegotiation. The
 * track is `live` and unmuted for the whole meeting and carries digital silence forever; real
 * participant audio arrives later on its own stream. lib-jitsi-meet refuses exactly these two ids
 * in `isUserStreamById` before treating a remote stream as a person.
 *
 * WHY THIS TEST EXISTS (measured on meet.ffmuc.net, 2026-09-17). A bot alone in a Jitsi room whose
 * only other participant was muted mirrored the placeholder and nothing else. Consequences, all
 * from that one mirrored stream:
 *   • `__vexaStreamPresence` reported 1 live remote stream while zero PCM frames ever crossed —
 *     row 4 of the deaf-capture guard's table (`aloneness.ts`), so the guard returned
 *     `capture-fault` and HELD the bot open on an empty room instead of resolving `left_alone`;
 *   • the bot's own log read `[mixed] capture started over 1 stream(s)`, indistinguishable from a
 *     working capture, which sent a live investigation after a transcription bug that did not exist.
 *
 * Pure DOM/WebRTC stubs — no browser, no meeting; the assertion is the mirroring bookkeeping.
 *
 * Run: npx tsx src/jitsi-placeholder.test.ts
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
// The real MediaStream carries an `id`; the placeholder is identified BY that id, so the stub must
// have one or the very discrimination under test is unobservable.
win.MediaStream = class {
  id: string; tracks: any[];
  constructor(t: any[] = [], id = 'stub-stream') { this.tracks = t; this.id = id; }
  getAudioTracks() { return this.tracks; }
};

class FakePC {
  private listeners: ((e: any) => void)[] = [];
  private _ontrack: ((e: any) => void) | null = null;
  addEventListener(type: string, fn: (e: any) => void): void { if (type === 'track') this.listeners.push(fn); }
  emit(event: any): void { for (const fn of this.listeners) fn(event); (this as any).ontrack?.(event); }
}
Object.defineProperty(FakePC.prototype, 'ontrack', {
  get(this: any) { return this._ontrack; },
  set(this: any, fn: any) { this._ontrack = fn; },
  configurable: true, enumerable: true,
});
win.RTCPeerConnection = FakePC as any;

// Imported as a NAMESPACE, deliberately: against a tree without the fix, a named import of a
// missing export is a module-load SyntaxError, and the run dies before a single behavioural
// assertion is reached. Through the namespace the predicate is simply absent, so the checks below
// report WHICH guarantee is missing instead of failing to start.
const hook: any = await import('./webrtc-audio-hook.js');
const { installRemoteAudioHook } = hook;
const isUserStreamId: (id?: string | null) => boolean = hook.isUserStreamId ?? (() => true);

// ── the predicate, stated directly ──
check("isUserStreamId('mixedmslabel') is false", isUserStreamId('mixedmslabel') === false);
check("isUserStreamId('default') is false", isUserStreamId('default') === false);
check("isUserStreamId('remote-audio-1') is true", isUserStreamId('remote-audio-1') === true);
check('isUserStreamId(undefined) is false (no id ⇒ no evidence of a participant)',
  isUserStreamId(undefined) === false);

// ── the hook, end to end ──
check('hook installs over the stubbed RTCPeerConnection', installRemoteAudioHook({ log: () => {} }) === true);
const pc: any = new (globalThis as any).RTCPeerConnection();
pc.ontrack = () => { /* the meeting app's own handler */ };

// 1. The placeholder arrives FIRST, exactly as Jitsi sends it at session-initiate.
const ph = { id: 'mixedlabelaudio0', kind: 'audio' };
pc.emit({ track: ph, streams: [new win.MediaStream([ph], 'mixedmslabel')] });
check('the mixedmslabel placeholder is NOT mirrored as a remote stream',
  win.__vexaCapturedRemoteAudioStreams.length === 0, `n=${win.__vexaCapturedRemoteAudioStreams.length}`);
check('the placeholder produces NO injected <audio> element (nothing for the recording tap to walk)',
  win.__vexaInjectedAudioElements.length === 0, `n=${win.__vexaInjectedAudioElements.length}`);

// 2. `default` — the same placeholder on older Jitsi builds.
const dflt = { id: 'defaultaudio0', kind: 'audio' };
pc.emit({ track: dflt, streams: [new win.MediaStream([dflt], 'default')] });
check("the 'default' placeholder is NOT mirrored either",
  win.__vexaCapturedRemoteAudioStreams.length === 0, `n=${win.__vexaCapturedRemoteAudioStreams.length}`);

// 3. The real participant then arrives on its own stream and MUST be mirrored — the refusal above
//    is a discrimination, not a mute button.
const real = { id: 'remote-audio-1', kind: 'audio' };
pc.emit({ track: real, streams: [new win.MediaStream([real], 'remote-audio-1')] });
check('the real participant stream IS mirrored',
  win.__vexaCapturedRemoteAudioStreams.length === 1, `n=${win.__vexaCapturedRemoteAudioStreams.length}`);
check('the real participant gets an injected <audio> element',
  win.__vexaInjectedAudioElements.length === 1, `n=${win.__vexaInjectedAudioElements.length}`);
check('the mirrored stream is the participant, not the placeholder',
  win.__vexaCapturedRemoteAudioStreams[0]?.id === 'remote-audio-1',
  String(win.__vexaCapturedRemoteAudioStreams[0]?.id));

// 4. A refused placeholder must not poison the dedup ledger: if a renegotiation ever reuses that
//    track id on a REAL stream, it still has to be mirrorable.
const reused = { id: 'mixedlabelaudio0', kind: 'audio' };
pc.emit({ track: reused, streams: [new win.MediaStream([reused], 'remote-audio-2')] });
check('a refused track id is not burned — the same id on a real stream still mirrors',
  win.__vexaCapturedRemoteAudioStreams.length === 2, `n=${win.__vexaCapturedRemoteAudioStreams.length}`);

// 5. A stream with no usable id is refused rather than guessed at.
const anon = { id: 'anon-1', kind: 'audio' };
pc.emit({ track: anon, streams: [new win.MediaStream([anon], '')] });
check('a stream with an empty id is refused (no id ⇒ no evidence of a participant)',
  win.__vexaCapturedRemoteAudioStreams.length === 2, `n=${win.__vexaCapturedRemoteAudioStreams.length}`);

if (failed) { console.error(`\n❌ jitsi-placeholder: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ jitsi-placeholder: Jitsi SDP placeholders never enter the mix, the recording tap, or the presence oracle.');

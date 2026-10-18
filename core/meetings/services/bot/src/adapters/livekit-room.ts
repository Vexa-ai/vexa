/**
 * The call's room — `CallRoom` over @livekit/rtc-node (Apache-2.0) (#1783).
 *
 * The listener joins with the hidden, subscribe-only token meeting-api minted (invocation.v1
 * `call.token`), subscribes to every participant's microphone track — and, unless the call's policy
 * turns it off (invocation.v1 `call.screenShareAudio`), the audio of a screen they share — and reads
 * each one as a stream of 16 kHz mono frames (`AudioStream`), which `../call.ts` feeds into the lane,
 * a shared screen on its own channel. Camera and screen video are never read. Participants already
 * in the room when the listener arrives are reported as joins.
 *
 * The SDK is injected (`sdk`) so the mapping is offline-provable; the live composition root passes
 * the real module.
 */
import type { CallAudioFrame, CallParticipant, CallRoom, CallRoomHandlers } from '../call.js';

/** The slice of @livekit/rtc-node this adapter uses. */
export interface LiveKitSdk {
  Room: new () => LkRoom;
  AudioStream: new (track: unknown, options: { sampleRate: number; numChannels: number }) => AsyncIterable<CallAudioFrame> & { cancel?: () => Promise<void> };
  RoomEvent: { ParticipantConnected: string; ParticipantDisconnected: string; TrackSubscribed: string; Disconnected: string };
  TrackKind: { KIND_AUDIO: unknown };
  /** The publication's source (rtc-ffi TrackSource). Optional: absent ⇒ the proto values below. */
  TrackSource?: { SOURCE_MICROPHONE: unknown; SOURCE_SCREENSHARE_AUDIO: unknown };
}
/** rtc-ffi `TrackSource` values (track.proto): SOURCE_MICROPHONE = 2, SOURCE_SCREENSHARE_AUDIO = 4. */
const SOURCE_MICROPHONE = 2;
const SOURCE_SCREENSHARE_AUDIO = 4;
export interface LkParticipant { identity: string; name?: string; disconnectReason?: number }
/** rtc-node's DisconnectReason.CLIENT_INITIATED: the participant pressed Leave (or closed the tab). */
const CLIENT_INITIATED = 1;
export interface LkRoom {
  remoteParticipants: Map<string, LkParticipant>;
  on(event: string, cb: (...args: any[]) => void): unknown;
  connect(url: string, token: string, opts?: { autoSubscribe?: boolean; dynacast?: boolean }): Promise<void>;
  disconnect(): Promise<void>;
}

const who = (p: LkParticipant): CallParticipant => ({ identity: p.identity, name: p.name || undefined });

export function createLiveKitRoom(opts: {
  sdk: LiveKitSdk; url: string; token: string; log?: (m: string) => void;
  /** Transcribe the audio of a shared screen (policy; default on). Off ⇒ it is not read at all. */
  screenShareAudio?: boolean;
}): CallRoom {
  const log = opts.log ?? (() => { /* quiet */ });
  const screenShareAudio = opts.screenShareAudio !== false;
  const MIC = opts.sdk.TrackSource?.SOURCE_MICROPHONE ?? SOURCE_MICROPHONE;
  const SCREEN_AUDIO = opts.sdk.TrackSource?.SOURCE_SCREENSHARE_AUDIO ?? SOURCE_SCREENSHARE_AUDIO;
  let room: LkRoom | null = null;
  const streams = new Set<{ cancel?: () => Promise<void> }>();

  const read = async (h: CallRoomHandlers, p: CallParticipant, track: unknown) => {
    const stream = new opts.sdk.AudioStream(track, { sampleRate: 16_000, numChannels: 1 });
    streams.add(stream);
    try {
      for await (const frame of stream) h.audio(p, frame);
    } catch (e) {
      log(`[bot] call: audio of ${p.identity} stopped: ${String(e)}`);
    } finally {
      streams.delete(stream);
    }
  };

  return {
    async connect(h) {
      const r = new opts.sdk.Room();
      room = r;
      const ev = opts.sdk.RoomEvent;
      r.on(ev.ParticipantConnected, (p: LkParticipant) => h.participantJoined(who(p)));
      r.on(ev.ParticipantDisconnected, (p: LkParticipant) =>
        h.participantLeft({ ...who(p), left: p.disconnectReason === CLIENT_INITIATED ? 'leave' : 'lost' }));
      r.on(ev.TrackSubscribed, (track: { kind?: unknown }, pub: { source?: unknown } | undefined, p: LkParticipant) => {
        if (track?.kind !== opts.sdk.TrackKind.KIND_AUDIO) return;
        const source = pub?.source;
        if (source === SCREEN_AUDIO) {
          if (!screenShareAudio) { log(`[bot] call: shared-screen audio of ${p.identity} not transcribed (policy off)`); return; }
          log(`[bot] call: transcribing the shared-screen audio of ${p.identity}`);
          void read(h, { ...who(p), source: 'screen_share_audio' }, track);
          return;
        }
        // A microphone, or a publication that does not say (older servers): the person's own voice.
        if (source !== undefined && source !== MIC && source !== 0) return;
        void read(h, who(p), track);
      });
      r.on(ev.Disconnected, (reason: unknown) => h.disconnected(String(reason)));
      await r.connect(opts.url, opts.token, { autoSubscribe: true, dynacast: false });
      for (const p of r.remoteParticipants.values()) h.participantJoined(who(p));
      log(`[bot] call: joined the room (${r.remoteParticipants.size} already there)`);
    },
    async disconnect() {
      for (const s of [...streams]) await s.cancel?.().catch(() => { /* best-effort */ });
      await room?.disconnect().catch(() => { /* best-effort */ });
      room = null;
    },
  };
}

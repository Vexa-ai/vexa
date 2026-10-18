/**
 * The call's room — `CallRoom` over @livekit/rtc-node (Apache-2.0) (#1783).
 *
 * The listener joins with the hidden, subscribe-only token meeting-api minted (invocation.v1
 * `call.token`), subscribes to every participant's microphone track, and reads each one as a
 * stream of 16 kHz mono frames (`AudioStream`), which `../call.ts` feeds into the lane. Camera and
 * screen tracks are never read. Participants already in the room when the listener arrives are
 * reported as joins.
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
}
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

export function createLiveKitRoom(opts: { sdk: LiveKitSdk; url: string; token: string; log?: (m: string) => void }): CallRoom {
  const log = opts.log ?? (() => { /* quiet */ });
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
      r.on(ev.TrackSubscribed, (track: { kind?: unknown }, _pub: unknown, p: LkParticipant) => {
        if (track?.kind !== opts.sdk.TrackKind.KIND_AUDIO) return;
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

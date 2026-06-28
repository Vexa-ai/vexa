/**
 * transcript.v1 egress ADAPTER — redis stream + pub/sub.
 *
 * Implements the `TranscriptSink` port. On each confirmed segment the engine pushes, this
 * fans out to BOTH legs of the 0.11 transcript transport:
 *
 *   1. STREAM  `transcription_segments`  (XADD * { payload })  — the durable feed the collector
 *      [Py] consumes. `payload` is JSON `{ type: 'transcription', ...segment }` (the segment
 *      fields spread alongside the discriminator, per the 0.11 collector wire format).
 *   2. PUB/SUB `tc:meeting:{meetingId}:mutable`  — the live mutable channel the gateway forwards
 *      to the dashboard. Message is JSON `{ type: 'transcript', meeting: { id }, segment }`.
 *
 * L3-testable via an INJECTED minimal `client` ({ xAdd, publish }) — no real redis. The factory
 * `redisClientFrom(url)` wraps node-redis v4 into that minimal interface for the composition root.
 */
import { createClient } from 'redis';
import type { TranscriptSegment } from '../contracts.js';
import type { TranscriptSink } from '../ports.js';

/** The redis stream the collector consumes (durable transcript.v1 feed). */
export const TRANSCRIPTION_STREAM = 'transcription_segments';

/** The live mutable pub/sub channel the gateway forwards to the dashboard. */
export const mutableChannel = (meetingId: string | number): string => `tc:meeting:${meetingId}:mutable`;

/** The minimal redis surface the sink needs — injected so the adapter is offline-provable. */
export interface RedisTranscriptClient {
  /** XADD key id fields — the live impl forwards to node-redis `xAdd`. */
  xAdd(key: string, id: string, fields: Record<string, string>): Promise<unknown>;
  /** PUBLISH channel message. */
  publish(channel: string, message: string): Promise<unknown>;
}

export interface RedisTranscriptSinkOptions {
  client: RedisTranscriptClient;
  /** The meeting id used in the mutable channel + bundle envelope. */
  meetingId: string | number;
  /** The native meeting code (e.g. `abc-defg-hij`). Stamped on the segment so the agent watcher keys
   *  on the native id WITHOUT a /meetings lookup (P23: one writer, no re-derivation). */
  nativeMeetingId?: string;
}

/** Build the live transcript sink. `publish` XADDs the durable feed AND publishes the live
 *  mutable channel for one segment (best-effort fan-out; rejections propagate to the engine,
 *  which decides whether a publish failure is fatal). */
export function createRedisTranscriptSink(opts: RedisTranscriptSinkOptions): TranscriptSink {
  const { client, meetingId, nativeMeetingId } = opts;
  const channel = mutableChannel(meetingId);

  async function publish(segment: TranscriptSegment): Promise<void> {
    // Leg 1: durable stream → collector. The collector's `ingest` REQUIRES the envelope
    // `{ type, meeting_id, segments:[…] }` — meeting_id to route the segment to its meeting, a
    // `segments` LIST to drain (a payload missing either is silently dropped: ingest.py `return 0`).
    // Emit that, not a flat segment, so the bot's transcripts actually reach the collector. (The
    // mock-bot L3 lane caught the flat form: O6 read the raw stream directly and never exercised the collector.)
    const payload = JSON.stringify({
      type: 'transcription', meeting_id: meetingId, native_meeting_id: nativeMeetingId, segments: [segment],
    });
    await client.xAdd(TRANSCRIPTION_STREAM, '*', { payload });

    // Leg 2: live mutable channel → gateway → dashboard.
    const msg = JSON.stringify({ type: 'transcript', meeting: { id: meetingId }, segment });
    await client.publish(channel, msg);
  }

  return { publish };
}

/** A live transcript client that also exposes connect/quit so the composition root can
 *  lazily connect and tear down. */
export type LiveRedisTranscriptClient = RedisTranscriptClient & {
  connect(): Promise<void>;
  quit(): Promise<void>;
};

/** Wrap node-redis v4 (`createClient`) into the minimal `RedisTranscriptClient`. Lazily
 *  connects on first use so the composition root can construct it before redis is reachable
 *  (the connection error surfaces on the first publish, not at construction). */
export function redisClientFrom(redisUrl: string): LiveRedisTranscriptClient {
  const client = createClient({ url: redisUrl });
  // node-redis emits 'error' events; without a listener an unreachable server throws unhandled.
  client.on('error', (err: unknown) => {
    console.error(`[bot] redis (transcript) error: ${(err as Error)?.message ?? String(err)}`);
  });
  let connected = false;
  const ensure = async (): Promise<void> => {
    if (!connected) {
      await client.connect();
      connected = true;
    }
  };
  return {
    async xAdd(key, id, fields) {
      await ensure();
      return client.xAdd(key, id, fields);
    },
    async publish(channel, message) {
      await ensure();
      return client.publish(channel, message);
    },
    async connect() {
      await ensure();
    },
    async quit() {
      if (connected) {
        await client.quit();
        connected = false;
      }
    },
  };
}

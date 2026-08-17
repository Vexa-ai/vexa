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
import { makeLazyConnect } from './redis-lazy-connect.js';

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
  /** Live WebSocket envelope. `speaker-snapshot` is the Dashboard's GMeet-compatible contract:
   * each message replaces the complete pending set for one stable speaker key while confirmed
   * rows remain additive. Keep the legacy one-segment envelope as the default for every platform
   * until its migration is proved independently. */
  liveEnvelope?: 'segment' | 'speaker-snapshot';
}

/** Build the live transcript sink. `publish` XADDs the durable feed AND publishes the live
 *  mutable channel for one segment (best-effort fan-out; rejections propagate to the engine,
 *  which decides whether a publish failure is fatal). */
export function createRedisTranscriptSink(opts: RedisTranscriptSinkOptions): TranscriptSink {
  const { client, meetingId, nativeMeetingId, liveEnvelope = 'segment' } = opts;
  const channel = mutableChannel(meetingId);
  const pendingBySpeakerKey = new Map<string, Map<string, TranscriptSegment>>();

  const speakerKeyFor = (segment: TranscriptSegment): string =>
    segment.speaker_key || segment.speaker || '';

  /** Mutate the local pending snapshot synchronously, before either Redis await, so concurrent
   * pipeline callbacks capture monotonically ordered snapshots rather than racing on I/O. */
  function liveMessageFor(segment: TranscriptSegment): string {
    if (liveEnvelope === 'segment') {
      return JSON.stringify({ type: 'transcript', meeting: { id: meetingId }, segment });
    }

    const speakerKey = speakerKeyFor(segment);
    const pending = pendingBySpeakerKey.get(speakerKey) ?? new Map<string, TranscriptSegment>();
    if (segment.completed) pending.delete(segment.segment_id);
    else pending.set(segment.segment_id, segment);
    if (pending.size > 0) pendingBySpeakerKey.set(speakerKey, pending);
    else pendingBySpeakerKey.delete(speakerKey);

    return JSON.stringify({
      type: 'transcript',
      meeting: { id: meetingId },
      // This field is the pending-snapshot identity, not a display label. CSRC-backed Teams rows
      // therefore stay isolated even while their human-facing `segment.speaker` is intentionally blank.
      speaker: speakerKey,
      confirmed: segment.completed ? [segment] : [],
      pending: [...pending.values()],
    });
  }

  async function publish(segment: TranscriptSegment): Promise<void> {
    const liveMessage = liveMessageFor(segment);
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
    await client.publish(channel, liveMessage);
  }

  /** Drop the withdrawn ids from every speaker's pending snapshot and report the keys that changed.
   * Synchronous by contract: `retract` calls this before its first await so a `publish` interleaving
   * with the durable XADD snapshots the map WITHOUT the retracted ids. Both callbacks are fired
   * fire-and-forget by the pipeline and the transcriber retracts then re-publishes one speaker key
   * in a single synchronous stack, so a map mutated after the await would let the stale snapshot
   * win the single-connection FIFO. */
  function withdrawFromPending(segmentIds: string[]): string[] {
    const ids = new Set(segmentIds);
    const changed: string[] = [];
    for (const [speakerKey, pending] of pendingBySpeakerKey) {
      let removed = false;
      for (const id of ids) removed = pending.delete(id) || removed;
      if (!removed) continue;
      if (pending.size === 0) pendingBySpeakerKey.delete(speakerKey);
      changed.push(speakerKey);
    }
    return changed;
  }

  /** Withdraw previously-published segments by id (a superseded/over-extended pending draft). Rides the
   *  SAME durable stream as `publish` so it's ordered with the segments it retracts — the collector
   *  deletes those rows and forwards a `retract` marker; the mutable channel carries it live too. */
  async function retract(segmentIds: string[]): Promise<void> {
    if (segmentIds.length === 0) return;
    // Snapshot bookkeeping happens HERE — before any await — so an interleaved publish() cannot
    // capture a pending set that still holds a retracted id.
    const changed = liveEnvelope === 'segment' ? [] : withdrawFromPending(segmentIds);

    const payload = JSON.stringify({
      type: 'transcript_retract', meeting_id: meetingId, native_meeting_id: nativeMeetingId, segment_ids: segmentIds,
    });
    await client.xAdd(TRANSCRIPTION_STREAM, '*', { payload });

    // The withdrawal itself is announced on the mutable channel in BOTH envelopes. A retracted id
    // may live in no pending map at all — a timeout-promoted draft that a later ownership check
    // rejects is a confirmed row — and a snapshot republish can only ever withdraw pending ones.
    // The `transcript_retract` message is the id-addressed withdrawal that reaches both lanes.
    const retractMessage = JSON.stringify({
      type: 'transcript_retract', meeting: { id: meetingId }, segment_ids: segmentIds,
    });
    await client.publish(channel, retractMessage);
    if (liveEnvelope === 'segment') return;

    // The Dashboard consumes full-replace pending snapshots, so every key the withdrawal touched
    // republishes its set. The set is read HERE, at publish time, not captured at withdrawal time:
    // a draft published for the same key while this call sat on the durable XADD belongs in the
    // snapshot, and either ordering of the two messages then leaves the same correct pending set.
    for (const speakerKey of changed) {
      const pending = pendingBySpeakerKey.get(speakerKey);
      await client.publish(channel, JSON.stringify({
        type: 'transcript', meeting: { id: meetingId }, speaker: speakerKey,
        confirmed: [], pending: pending ? [...pending.values()] : [],
      }));
    }
  }

  return { publish, retract };
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
  // Idempotent lazy connect (shared with the acts subscriber): concurrent first-use callers share
  // ONE connect(), so the "Socket already opened" first-use race can't recur. See redis-lazy-connect.ts.
  const lazy = makeLazyConnect(client);
  return {
    async xAdd(key, id, fields) {
      await lazy.ensure();
      return client.xAdd(key, id, fields);
    },
    async publish(channel, message) {
      await lazy.ensure();
      return client.publish(channel, message);
    },
    async connect() {
      await lazy.ensure();
    },
    async quit() {
      await lazy.quit();
    },
  };
}

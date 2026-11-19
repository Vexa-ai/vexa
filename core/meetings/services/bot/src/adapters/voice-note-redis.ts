/**
 * The voice note's stream reader — `AudioStreamReader` over Redis XREAD (#1783).
 *
 * meeting-api appends each capture.v1 frame the browser sent to `voice_note:meeting:{id}:audio`
 * (field `f`, the frame's bytes), a keepalive (field `alive`) while a note is paused and, when the
 * person says Stop, one entry with field `end`. This
 * adapter reads that stream for `../voice-note.ts`. Binary-safe: frames are read as buffers.
 *
 * A dedicated connection: XREAD BLOCK holds its connection for the whole block, so it must not share
 * one with the transcript writer or the acts subscriber. The bot's Redis user may read this one key
 * and no other (meeting-api `workload_redis.acl_rules`).
 */
import { commandOptions, createClient } from 'redis';
import type { AudioStreamReader, StreamEntry } from '../voice-note.js';
import { makeLazyConnect } from './redis-lazy-connect.js';
import { botReconnectStrategy, type BotRedisOptions } from './redis-terminal.js';

/** The XREAD reply shape node-redis v4 returns (with returnBuffers, ids and values are Buffers). */
export type XReadReply = Array<{ name: unknown; messages: Array<{ id: unknown; message: Record<string, unknown> }> }> | null;

/** The minimal surface the reader needs — injected so the adapter is offline-provable. */
export interface RedisStreamClient {
  xReadBuffers(key: string, afterId: string, blockMs: number, count: number): Promise<XReadReply>;
}

const text = (v: unknown): string => (typeof v === 'string' ? v : Buffer.isBuffer(v) ? v.toString('utf8') : String(v));
const bytes = (v: unknown): Uint8Array | undefined =>
  Buffer.isBuffer(v) ? new Uint8Array(v.buffer, v.byteOffset, v.byteLength)
    : v instanceof Uint8Array ? v
      : undefined;

/** Map an XREAD reply onto stream entries: `f` is a frame, `end` is the end of the note. */
export function entriesFrom(reply: XReadReply): StreamEntry[] {
  const out: StreamEntry[] = [];
  for (const stream of reply ?? []) {
    for (const m of stream.messages ?? []) {
      const id = text(m.id);
      if ('end' in m.message) out.push({ id, end: true });
      else if ('alive' in m.message) out.push({ id, alive: true });
      else out.push({ id, frame: bytes(m.message.f) });
    }
  }
  return out;
}

export function createRedisAudioStreamReader(opts: { client: RedisStreamClient; key: string; count?: number }): AudioStreamReader {
  const count = opts.count ?? 64;
  return {
    async read(afterId, blockMs) {
      return entriesFrom(await opts.client.xReadBuffers(opts.key, afterId, blockMs, count));
    },
  };
}

export type LiveRedisStreamClient = RedisStreamClient & { quit(): Promise<void> };

/** A dedicated node-redis v4 connection for the reader, connected on first read. It stops
 *  reconnecting once the session is over (`redis-terminal.ts`), so a revoked credential ends it. */
export function redisStreamClientFrom(redisUrl: string, opts: BotRedisOptions = {}): LiveRedisStreamClient {
  const client = createClient({ url: redisUrl, socket: { reconnectStrategy: botReconnectStrategy(opts.session) } });
  client.on('error', (err: unknown) => {
    console.error(`[bot] redis (voice note) error: ${(err as Error)?.message ?? String(err)}`);
  });
  const lazy = makeLazyConnect(client);
  return {
    async xReadBuffers(key, afterId, blockMs, count) {
      await lazy.ensure();
      return (await client.xRead(
        commandOptions({ returnBuffers: true }),
        { key, id: afterId },
        { BLOCK: blockMs, COUNT: count },
      )) as unknown as XReadReply;
    },
    async quit() { await lazy.quit(); },
  };
}

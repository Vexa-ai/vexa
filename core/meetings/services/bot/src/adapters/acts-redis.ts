/**
 * acts.v1 ingress ADAPTER — redis pub/sub subscriber.
 *
 * Implements the `ActsSource` port. SUBSCRIBEs to the meeting's command bus
 * `bot_commands:meeting:{meetingId}` (the channel `actsChannel(meetingId)` already defines in
 * contracts.ts), JSON.parses each message, runs it through the bot's existing `parseAct`
 * narrowing (so off-contract / unknown-action messages are IGNORED, never thrown), and calls
 * `handler(act)` for every recognized Act.
 *
 * L3-testable via an INJECTED minimal `client` ({ subscribe, unsubscribe? }) — no real redis.
 * The factory `redisActsClientFrom(url)` wraps a node-redis v4 SUBSCRIBER connection.
 */
import { createClient } from 'redis';
import { actsChannel, parseAct, type Act } from '../contracts.js';
import type { ActsSource } from '../ports.js';
import { makeLazyConnect } from './redis-lazy-connect.js';

/** The minimal subscriber surface the source needs — injected so the adapter is offline-provable.
 *  `subscribe(channel, cb)` delivers each raw message string to `cb`. */
export interface RedisActsClient {
  subscribe(channel: string, cb: (message: string) => void): void | Promise<void>;
  /** Best-effort unsubscribe / teardown; optional. */
  unsubscribe?(channel?: string): void | Promise<void>;
}

export interface RedisActsSourceOptions {
  client: RedisActsClient;
  /** The meeting id whose command channel to subscribe to. */
  meetingId: string | number;
}

/** Build the live acts source. `subscribe(handler)` SUBSCRIBEs the meeting's command channel
 *  and dispatches each schema-recognized Act to `handler`; returns an unsubscribe fn. A
 *  malformed / unknown message is logged + dropped — it never throws out of the message path. */
export function createRedisActsSource(opts: RedisActsSourceOptions): ActsSource {
  const { client, meetingId } = opts;
  const channel = actsChannel(meetingId);

  function subscribe(handler: (act: Act) => void | Promise<void>): () => void {
    const onMessage = (message: string): void => {
      let decoded: unknown;
      try {
        decoded = JSON.parse(message);
      } catch {
        console.error(`[bot] acts.v1: dropped non-JSON message on ${channel}`);
        return;
      }
      const act = parseAct(decoded);
      if (!act) return; // off-contract or unknown action → ignored (acts.v1 README)
      void Promise.resolve(handler(act)).catch((e) => {
        console.error(`[bot] acts.v1: handler for '${act.action}' rejected: ${String(e)}`);
      });
    };

    // node-redis `subscribe` may be async; we don't await here (the port returns a sync
    // unsubscribe fn). Surface a subscribe failure via the error log rather than throwing.
    void Promise.resolve(client.subscribe(channel, onMessage)).catch((e) => {
      console.error(`[bot] acts.v1: subscribe to ${channel} failed: ${String(e)}`);
    });

    return () => {
      void Promise.resolve(client.unsubscribe?.(channel)).catch(() => {
        /* best-effort teardown */
      });
    };
  }

  return { subscribe };
}

/** A live acts client that also exposes connect/quit so the composition root can lazily
 *  connect and tear down. */
export type LiveRedisActsClient = RedisActsClient & {
  connect(): Promise<void>;
  quit(): Promise<void>;
};

/** Wrap a node-redis v4 SUBSCRIBER connection into the minimal `RedisActsClient`. A subscriber
 *  needs its own dedicated connection (v4 reserves a subscribed client for pub/sub), so this is
 *  a separate client from the transcript writer. Connects lazily on first subscribe. */
export function redisActsClientFrom(redisUrl: string): LiveRedisActsClient {
  const client = createClient({ url: redisUrl });
  client.on('error', (err: unknown) => {
    console.error(`[bot] redis (acts) error: ${(err as Error)?.message ?? String(err)}`);
  });
  // Idempotent lazy connect (shared with the transcript writer): concurrent first-use callers share
  // ONE connect(), so the "Socket already opened" first-use race can't recur. See redis-lazy-connect.ts.
  const lazy = makeLazyConnect(client);
  return {
    async subscribe(channel, cb) {
      await lazy.ensure();
      // node-redis v4: subscribe(channel, listener) — listener receives the message string.
      await client.subscribe(channel, (message: string) => cb(message));
    },
    async unsubscribe(channel) {
      if (client.isOpen) await client.unsubscribe(channel);
    },
    async connect() {
      await lazy.ensure();
    },
    async quit() {
      await lazy.quit();
    },
  };
}

/** ALLOY: Redis publication boundary for opt-in local STT queue diagnostics. */
import type { AlloySttTelemetrySnapshotV1 } from '@vexa/gmeet-pipeline';
import { createClient } from 'redis';
import { makeLazyConnect } from './redis-lazy-connect.js';

export const alloySttTelemetryKey = (meetingId: string | number): string =>
  `alloy:stt:telemetry:v1:${meetingId}`;

export interface AlloySttTelemetryRedisClient {
  set(
    key: string,
    value: string,
    options: { EX: number },
  ): Promise<unknown>;
  del(key: string): Promise<unknown>;
}

export interface AlloySttTelemetryPublisher {
  start(): void;
  publishNow(): Promise<void>;
  stop(): Promise<void>;
}

export type LiveAlloySttTelemetryRedisClient = AlloySttTelemetryRedisClient & {
  quit(): Promise<void>;
};

export async function closeAlloySttTelemetryRedisClient(
  client: { quit(): Promise<unknown>; destroy?(): void },
  timeoutMs = 500,
): Promise<void> {
  let closed = false;
  await Promise.race([
    Promise.resolve()
      .then(() => client.quit())
      .then(
        () => {
          closed = true;
        },
        () => undefined,
      ),
    new Promise<void>((resolve) => setTimeout(resolve, Math.max(10, timeoutMs))),
  ]);
  if (!closed) client.destroy?.();
}

export function alloySttTelemetryRedisClientFrom(
  redisUrl: string,
): LiveAlloySttTelemetryRedisClient {
  const client = createClient({ url: redisUrl });
  client.on('error', (error: unknown) => {
    console.error(
      `[ALLOY] Redis STT telemetry error: ${(error as Error)?.message ?? String(error)}`,
    );
  });
  const lazy = makeLazyConnect(client);
  return {
    async set(key, value, options) {
      await lazy.ensure();
      return client.set(key, value, options);
    },
    async del(key) {
      await lazy.ensure();
      return client.del(key);
    },
    async quit() {
      await closeAlloySttTelemetryRedisClient({
        quit: () => lazy.quit(),
        destroy: () => {
          (client as unknown as { destroy?: () => void }).destroy?.();
        },
      });
    },
  };
}

export function createAlloySttTelemetryPublisher(opts: {
  client: AlloySttTelemetryRedisClient;
  meetingId: string | number;
  readSnapshot: () => AlloySttTelemetrySnapshotV1;
  intervalMs?: number;
  ttlSec?: number;
  stopTimeoutMs?: number;
  onError?: (error: unknown) => void;
}): AlloySttTelemetryPublisher {
  const intervalMs = Math.max(10, opts.intervalMs ?? 1_000);
  const ttlSec = Math.max(2, opts.ttlSec ?? 15);
  const stopTimeoutMs = Math.max(10, opts.stopTimeoutMs ?? 500);
  const key = alloySttTelemetryKey(opts.meetingId);
  let timer: ReturnType<typeof setInterval> | null = null;
  let inFlight: Promise<void> | null = null;

  const report = (error: unknown): void => {
    opts.onError?.(error);
  };

  const settleWithinStopBudget = async (
    operation: Promise<unknown> | null,
  ): Promise<void> => {
    if (!operation) return;
    await Promise.race([
      operation.then(() => undefined, report),
      new Promise<void>((resolve) => {
        const timeout = setTimeout(resolve, stopTimeoutMs);
        (timeout as { unref?: () => void }).unref?.();
      }),
    ]);
  };

  const publishNow = (): Promise<void> => {
    if (inFlight) return inFlight;
    inFlight = opts.client
      .set(key, JSON.stringify(opts.readSnapshot()), { EX: ttlSec })
      .then(() => undefined)
      .catch(report)
      .finally(() => {
        inFlight = null;
      });
    return inFlight;
  };

  return {
    start(): void {
      if (timer) return;
      void publishNow();
      timer = setInterval(() => {
        if (!inFlight) void publishNow();
      }, intervalMs);
      (timer as { unref?: () => void }).unref?.();
    },

    publishNow,

    async stop(): Promise<void> {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
      // ALLOY: optional diagnostics must never hold bot teardown on a stalled Redis socket.
      await settleWithinStopBudget(inFlight);
      await settleWithinStopBudget(opts.client.del(key));
    },
  };
}

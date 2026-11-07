/**
 * When the bot's Redis clients stop reconnecting.
 *
 * Each bot connects as a Redis user of its own session, and meeting-api removes that user on the
 * terminal callback (`bot_spawn/workload_redis.py` `revoke`). From then on every reconnect is refused
 * with WRONGPASS. node-redis v4 reconnects forever by default, so a finished bot sat in that loop:
 * a pending command never settled, `quit()` queued a QUIT that could never be sent, teardown never
 * finished and the process never exited.
 *
 * The rule: while the session runs, keep reconnecting — a restarted Redis has lost every ACL user
 * until meeting-api restores them, and a live bot must ride that out. Once the session is over, an
 * auth failure is terminal at once and any other failure after a few tries. A terminal answer makes
 * node-redis close the socket and reject every queued command, so teardown proceeds.
 */

/** WRONGPASS (credential gone) and NOAUTH (none accepted) — the user this bot connects as is gone. */
export function isRedisAuthError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? '');
  return /^(WRONGPASS|NOAUTH)\b/.test(msg);
}

/** The session's end, as the Redis clients see it: `end()` once the orchestrator has finished. */
export interface SessionEnd {
  end(): void;
  readonly ended: boolean;
}

export function createSessionEnd(): SessionEnd {
  let ended = false;
  return {
    end() { ended = true; },
    get ended() { return ended; },
  };
}

/** Reconnect attempts after the session is over, for a failure other than auth. */
export const AFTER_END_MAX_RETRIES = 3;

/** node-redis v4 `socket.reconnectStrategy`: a delay in ms to retry, or an Error to stop. */
export type ReconnectStrategy = (retries: number, cause: Error) => number | Error;

/** Same backoff as node-redis's default while the session runs; terminal as above once it is over. */
export function botReconnectStrategy(session?: Pick<SessionEnd, 'ended'>): ReconnectStrategy {
  return (retries, cause) => {
    if (session?.ended) {
      if (isRedisAuthError(cause)) {
        return new Error(`redis: the session is over and its credential is gone — not reconnecting (${cause.message})`);
      }
      if (retries >= AFTER_END_MAX_RETRIES) {
        return new Error(`redis: the session is over — not reconnecting after ${retries} tries (${cause?.message ?? cause})`);
      }
    }
    return Math.min(retries * 50, 500);
  };
}

/** The options the bot's client factories take. */
export interface BotRedisOptions {
  /** Ended when the session is over; omitted, the client reconnects as node-redis does by default. */
  session?: Pick<SessionEnd, 'ended'>;
}

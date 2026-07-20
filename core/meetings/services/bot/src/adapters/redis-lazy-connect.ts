/**
 * Idempotent lazy connect for the bot's node-redis v4 clients (transcript writer + acts subscriber).
 *
 * Both factories connect on FIRST use so the composition root can construct them before redis is
 * reachable. The naive memo — a boolean flipped only AFTER `connect()` resolves — is a check-then-act
 * race: the first `await client.connect()` yields the loop before the flag is set, so a second
 * concurrent first-use caller also sees the flag false and calls `connect()` again. node-redis v4
 * throws `Socket already opened` on that second call (observed as a bot pipeline fault). Memoizing
 * the connect PROMISE (not a post-hoc boolean) makes concurrent first-use callers share ONE
 * `connect()`; a failed connect clears the memo so a later call can retry.
 *
 * L3-testable in isolation (redis-lazy-connect.test.ts) via a fake `LazyConnectable` — no real redis.
 */

/** The minimal node-redis surface the memo drives. node-redis's client satisfies this structurally
 *  (`connect`/`quit` return promises; `isOpen` is a boolean getter). */
export interface LazyConnectable {
  connect(): Promise<unknown>;
  quit(): Promise<unknown>;
  readonly isOpen: boolean;
}

export interface LazyConnect {
  /** Connect on first call; concurrent first-use callers all await the SAME connect(). */
  ensure(): Promise<void>;
  /** Settle any in-flight connect first (so quit never races a half-open socket), then quit()
   *  iff the socket is actually open. Resets the memo so a later ensure() can reconnect. */
  quit(): Promise<void>;
}

export function makeLazyConnect(client: LazyConnectable): LazyConnect {
  let connecting: Promise<void> | null = null;
  const ensure = (): Promise<void> => {
    if (!connecting) {
      connecting = Promise.resolve(client.connect()).then(
        () => undefined,
        (err) => {
          connecting = null; // failed connect is not sticky — a later ensure() retries
          throw err;
        },
      );
    }
    return connecting;
  };
  return {
    ensure,
    async quit() {
      if (connecting) await connecting.catch(() => undefined);
      if (client.isOpen) await client.quit();
      connecting = null;
    },
  };
}

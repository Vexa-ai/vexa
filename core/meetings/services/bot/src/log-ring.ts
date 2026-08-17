/**
 * The bot's own recent console lines, kept in memory so a FAILED terminal can carry them out.
 *
 * A bot container is deleted minutes after it dies, so its console — the only running commentary
 * that explains WHY a join failed — dies with it. `meetings.data.bot_logs` existed for exactly
 * this and was fed by the pre-0.12 parent; the rewritten lifecycle carries the sink (meeting-api
 * trims the buffer to 50 KiB oldest-first and omits it from list responses) but nothing ever fed
 * it, so every join-failure investigation since 2026-07-18 has had zero forensic material (#1189).
 *
 * This is the PRODUCER half: a byte-capped ring over the console, consulted once when the
 * orchestrator builds a `failed` terminal event.
 *
 * Distinct from `telemetry.ts`'s `startBotLogSidecar`, and deliberately not merged with it:
 *   * the sidecar writes a 50 MB head+tail FILE beside a capture tape, and only exists when
 *     capture-signal recording is on (off in production);
 *   * this ring holds ≤50 KiB in memory, always, and its consumer is the lifecycle callback.
 * Both tap `console` by wrapping the previous method, so they compose in either order — each
 * delegates to whatever it replaced. Restore is LIFO: stop the sidecar first, this second.
 *
 * A log tap must never break the bot: every write is swallowed, and `tail()` cannot throw.
 */

/** Bytes of tail carried on a failure terminal. Mirrors meeting-api's `_BOT_LOGS_BYTE_BUDGET`
 *  (`lifecycle/machine.py`) so the wire payload is already inside the budget the sink enforces —
 *  the sink stays the enforcer, this just avoids POSTing megabytes for it to throw away. */
export const BOT_LOG_TAIL_BYTE_BUDGET = 50 * 1024;

export interface BotLogRing {
  /** The kept lines, OLDEST → NEWEST (the order `bot_logs` is read in). Never throws. */
  tail(): string[];
  /** True once the ring has evicted at least one line. */
  truncated(): boolean;
  /** Restore the console methods this ring replaced. Idempotent. */
  stop(): void;
}

const METHODS = ['log', 'info', 'warn', 'error', 'debug'] as const;

/**
 * Start taping `console` into a byte-capped ring. Returns the ring; call `stop()` at teardown.
 *
 * Lines are `<iso> <LEVEL> <body>` with no trailing newline — meeting-api's trimmer charges one
 * byte per line for the implicit newline, so the two budgets agree.
 */
export function startBotLogRing(
  opts: { maxBytes?: number; console?: Partial<Console>; now?: () => number } = {},
): BotLogRing {
  const maxBytes = opts.maxBytes ?? BOT_LOG_TAIL_BYTE_BUDGET;
  const target = (opts.console ?? console) as Record<string, (...a: unknown[]) => void>;
  const original: Partial<Record<string, (...a: unknown[]) => void>> = {};
  let lines: string[] = [];
  let bytes = 0;
  let dropped = 0;
  let stopped = false;

  const sizeOf = (line: string): number => Buffer.byteLength(line, 'utf8') + 1; // +1: implicit newline

  const push = (line: string): void => {
    lines.push(line);
    bytes += sizeOf(line);
    // Evict the OLDEST first — the lines nearest the failure are the ones worth carrying. Always
    // keep at least one line, so a single over-budget line still says something rather than nothing.
    while (bytes > maxBytes && lines.length > 1) {
      bytes -= sizeOf(lines.shift()!);
      dropped++;
    }
  };

  const fmt = (level: string, args: unknown[]): string => {
    const body = args.map((a) => {
      if (typeof a === 'string') return a;
      try { return JSON.stringify(a); } catch { return String(a); }
    }).join(' ');
    return `${new Date(opts.now?.() ?? Date.now()).toISOString()} ${level} ${body}`;
  };

  for (const m of METHODS) {
    const prev = target[m];
    if (typeof prev !== 'function') continue;
    original[m] = prev;
    target[m] = (...args: unknown[]): void => {
      try { push(fmt(m.toUpperCase(), args)); } catch { /* a log tap must never break the bot */ }
      prev.apply(target, args);
    };
  }

  return {
    tail: () => lines.slice(),
    truncated: () => dropped > 0,
    stop(): void {
      if (stopped) return;
      stopped = true;
      for (const m of METHODS) if (original[m]) target[m] = original[m]!;
    },
  };
}

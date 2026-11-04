/**
 * SIGTERM/SIGINT wiring for the disposable worker (P7) — extracted from the composition root so
 * it is unit-testable (signals.test.ts).
 *
 * The release-eyeball incident: `docker stop -t 30` on live bots ended in exit 137 (SIGKILL) —
 * the graceful leave never ran, so the bot stayed a ghost attendee until the daemon killed it
 * mid-capture. Two halves fix that: the entrypoint FORWARDS the signal to node (entrypoint.sh —
 * bash as PID 1 forwarded nothing), and this module makes the handling BOUNDED:
 *
 *   1. a termination signal triggers the orchestrator's graceful end (`stop('stopped')`): leave
 *      the meeting → stop the pipeline → flush the recording → POST the terminal lifecycle
 *      callback → exit 0 through the normal run() path (the orchestrator already caps the
 *      platform leave itself at 8s);
 *   2. a force-exit WATCHDOG is armed (default 20s — inside the runtime's SIGTERM→SIGKILL stop
 *      grace, RUNTIME_STOP_GRACE_SEC=30): if the graceful path wedges (a hung browser teardown,
 *      a stuck upload), exit 1 — honest (the leave did NOT complete) but with the terminal
 *      callback given every chance, instead of the silent 137.
 *
 * The watchdog timer is unref'd: it never keeps a cleanly-finishing process alive, and it
 * deliberately SURVIVES `release()` (which only detaches the signal listeners) so a teardown that
 * hangs AFTER run() resolved still cannot outlive the grace.
 */

export const DEFAULT_SIGTERM_GRACE_MS = 20_000;

/** The bounded grace for a signal-triggered leave (<25s, under the runtime's 30s stop grace).
 *  Override with BOT_SIGTERM_GRACE_MS (ms). */
export function sigtermGraceMs(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number(env.BOT_SIGTERM_GRACE_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_SIGTERM_GRACE_MS;
}

/** The slice of `process` the handler needs — injectable so tests drive a fake. */
export interface SignalTarget {
  once(event: string, handler: () => void): unknown;
  off(event: string, handler: () => void): unknown;
  exit(code: number): void;
}

export interface SignalOptions {
  /** The graceful end — the orchestrator's `stop` (resolves run() → completed(stopped) → exit 0). */
  stop: (reason: 'stopped') => void;
  /** Force-exit bound in ms; defaults to BOT_SIGTERM_GRACE_MS / 20s. */
  graceMs?: number;
  /** Injectable process stand-in (tests); defaults to the real `process`. */
  proc?: SignalTarget;
  log?: (msg: string) => void;
}

/**
 * Install SIGTERM/SIGINT handlers. Returns `release()` — detaches the listeners (call once run()
 * resolved, so a late signal can't touch a dead orchestrator); an already-armed watchdog stays.
 */
export function installSignalHandlers(opts: SignalOptions): () => void {
  const proc = opts.proc ?? (process as unknown as SignalTarget);
  const graceMs = opts.graceMs ?? sigtermGraceMs();
  const log = opts.log ?? ((m: string) => console.error(m));
  let watchdog: ReturnType<typeof setTimeout> | null = null;

  const onSignal = () => {
    log(`[bot] termination signal — triggering graceful leave (force-exit watchdog ${graceMs}ms)`);
    try {
      opts.stop('stopped');
    } finally {
      if (watchdog == null) {
        watchdog = setTimeout(() => {
          log(`[bot] graceful leave did not complete within ${graceMs}ms — force exit 1`);
          proc.exit(1);
        }, graceMs);
        watchdog.unref?.();
      }
    }
  };

  proc.once('SIGTERM', onSignal);
  proc.once('SIGINT', onSignal);
  return () => {
    proc.off('SIGTERM', onSignal);
    proc.off('SIGINT', onSignal);
  };
}

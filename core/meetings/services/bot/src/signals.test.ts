/**
 * L2 — SIGTERM/SIGINT handling (signals.ts), offline against a fake process.
 *
 * The release-eyeball incident: `docker stop -t 30` on live bots exited 137 — no graceful leave.
 * Asserts the node half of the fix:
 *   • SIGTERM triggers the orchestrator's graceful end (`stop('stopped')`) exactly once;
 *   • the force-exit watchdog fires exit(1) when the graceful path wedges — BOUNDED, never a hang
 *     that rides into the daemon's SIGKILL;
 *   • the watchdog does NOT fire when the worker finishes inside the grace (a clean leave keeps
 *     its normal exit code);
 *   • SIGINT is wired identically; release() detaches the listeners;
 *   • the grace resolver honours BOT_SIGTERM_GRACE_MS and stays under 25s by default.
 * No browser / redis / signals to the real process. Run: npx tsx src/signals.test.ts
 */
import { DEFAULT_SIGTERM_GRACE_MS, installSignalHandlers, sigtermGraceMs } from './signals.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** A fake `process`: records exit() calls, lets the test emit signals. */
class FakeProc {
  handlers = new Map<string, Array<() => void>>();
  exits: number[] = [];
  once(event: string, handler: () => void) {
    // Mirror Node's EventEmitter.once: wrap for one-shot delivery, but remember the original so
    // `off(event, original)` (what release() calls) removes the wrapper — exactly like process.off.
    const wrapped = () => {
      this.off(event, handler);
      handler();
    };
    (wrapped as { original?: () => void }).original = handler;
    if (!this.handlers.has(event)) this.handlers.set(event, []);
    this.handlers.get(event)!.push(wrapped);
    return this;
  }
  off(event: string, handler: () => void) {
    const hs = this.handlers.get(event) ?? [];
    this.handlers.set(event, hs.filter(
      (h) => h !== handler && (h as { original?: () => void }).original !== handler,
    ));
    return this;
  }
  emit(event: string) {
    for (const h of [...(this.handlers.get(event) ?? [])]) h();
  }
  exit(code: number) {
    this.exits.push(code);
  }
  listenerCount(event: string) {
    return (this.handlers.get(event) ?? []).length;
  }
}

const main = async () => {
  // ── SIGTERM triggers the graceful stop; a wedged worker is force-exited within the grace ──
  {
    const proc = new FakeProc();
    const stops: string[] = [];
    installSignalHandlers({
      stop: (r) => stops.push(r), graceMs: 40, proc, log: () => {},
    });
    proc.emit('SIGTERM');
    check('SIGTERM → orchestrator.stop("stopped")', stops.length === 1 && stops[0] === 'stopped',
      JSON.stringify(stops));
    check('no premature force-exit', proc.exits.length === 0, JSON.stringify(proc.exits));
    await sleep(80); // the graceful path never completes → the watchdog must bound the exit
    check('wedged leave → force exit(1) within the grace', proc.exits.length === 1 && proc.exits[0] === 1,
      JSON.stringify(proc.exits));
    proc.emit('SIGTERM');
    check('second signal is a no-op (once + single watchdog)', stops.length === 1 && proc.exits.length === 1);
  }

  // ── a leave that COMPLETES inside the grace: release() detaches, normal exit path wins ──
  {
    const proc = new FakeProc();
    let stopped = false;
    const release = installSignalHandlers({
      stop: () => { stopped = true; }, graceMs: 5_000, proc, log: () => {},
    });
    proc.emit('SIGTERM');
    check('graceful stop triggered', stopped);
    release(); // run() resolved (the orchestrator completed + flushed) — worker exits normally
    check('release() detaches listeners', proc.listenerCount('SIGTERM') === 0 && proc.listenerCount('SIGINT') === 0);
    check('no force-exit for a clean leave (watchdog is unref’d & the process exits normally)',
      proc.exits.length === 0);
  }

  // ── SIGINT wired identically ──
  {
    const proc = new FakeProc();
    const stops: string[] = [];
    installSignalHandlers({ stop: (r) => stops.push(r), graceMs: 5_000, proc, log: () => {} });
    proc.emit('SIGINT');
    check('SIGINT → orchestrator.stop("stopped")', stops.length === 1 && stops[0] === 'stopped');
  }

  // ── grace resolver: env override; default bounded under the runtime's 30s stop grace ──
  check('default grace = 20s (<25s bound)', DEFAULT_SIGTERM_GRACE_MS === 20_000 && DEFAULT_SIGTERM_GRACE_MS < 25_000);
  check('BOT_SIGTERM_GRACE_MS override', sigtermGraceMs({ BOT_SIGTERM_GRACE_MS: '12000' } as never) === 12_000);
  check('bad override falls back to default', sigtermGraceMs({ BOT_SIGTERM_GRACE_MS: 'nope' } as never) === 20_000);
  check('non-positive override falls back to default', sigtermGraceMs({ BOT_SIGTERM_GRACE_MS: '0' } as never) === 20_000);
};

main().then(() => {
  if (failed) { console.error(`\n❌ signals (L2): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ signals (L2): SIGTERM/SIGINT trigger the graceful leave, and the force-exit watchdog bounds a wedged teardown inside the stop grace.');
});

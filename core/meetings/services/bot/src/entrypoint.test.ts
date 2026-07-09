/**
 * L2 — entrypoint.sh SIGNAL FORWARDING (the exit-137 fix), against the REAL entrypoint script.
 *
 * The incident: `docker stop -t 30` on live bots exited 137 — bash ran as PID 1 with node in the
 * foreground, so the SIGTERM reached nobody and the daemon SIGKILLed the whole container; the
 * graceful leave never ran. This test runs the shipped entrypoint.sh (X11/Pulse binaries stubbed
 * via PATH; BOT_APP_DIR/BOT_WORKER_ENTRY point at a stub worker) as a process-group leader —
 * docker's PID-1 delivery — and asserts:
 *   • SIGTERM to the entrypoint reaches the WORKER (its handler runs a graceful "leave");
 *   • the entrypoint exits with the WORKER's exit code (0 for a clean leave) — not 143/137;
 *   • a normal worker exit still propagates its code unchanged (breadcrumbs preserved).
 * Needs bash + node on PATH (the image has both). Run: npx tsx src/entrypoint.test.ts
 */
import { spawn } from 'node:child_process';
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ENTRYPOINT = join(HERE, '..', 'entrypoint.sh');

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

/** Stub the X11/audio bringup (Xvfb, fluxbox, pulseaudio, pactl) as no-ops on PATH. */
function makeStubBin(root: string): string {
  const bin = join(root, 'bin');
  mkdirSync(bin, { recursive: true });
  for (const name of ['Xvfb', 'fluxbox', 'pulseaudio', 'pactl']) {
    const p = join(bin, name);
    writeFileSync(p, '#!/bin/sh\nexit 0\n');
    chmodSync(p, 0o755);
  }
  return bin;
}

interface RunResult { code: number | null; stdout: string; }

/** Run the real entrypoint.sh with a stub worker; optionally SIGTERM the LEADER (docker-style). */
function runEntrypoint(workerJs: string, opts: { sigtermAfterMs?: number } = {}): Promise<RunResult> {
  const root = mkdtempSync(join(tmpdir(), 'bot-entrypoint-'));
  const bin = makeStubBin(root);
  const appDir = join(root, 'app');
  mkdirSync(appDir, { recursive: true });
  writeFileSync(join(appDir, 'worker.cjs'), workerJs);

  const child = spawn('bash', [ENTRYPOINT], {
    env: {
      ...process.env,
      PATH: `${bin}:${process.env.PATH ?? ''}`,
      BOT_APP_DIR: appDir,
      BOT_WORKER_ENTRY: 'worker.cjs',
      DISPLAY: ':99',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true, // its own process group — SIGTERM hits ONLY the leader, like docker's PID 1
  });

  let stdout = '';
  child.stdout.on('data', (d) => { stdout += String(d); });
  child.stderr.on('data', (d) => { stdout += String(d); });

  if (opts.sigtermAfterMs != null) {
    // Wait for the worker's READY breadcrumb, then signal the LEADER only (what docker stop does).
    const t0 = Date.now();
    const poll = setInterval(() => {
      if (stdout.includes('WORKER-READY')) {
        clearInterval(poll);
        try { child.kill('SIGTERM'); } catch { /* already gone */ }
      } else if (Date.now() - t0 > 15_000) {
        clearInterval(poll);
        try { process.kill(-child.pid!, 'SIGKILL'); } catch { /* already gone */ }
      }
    }, opts.sigtermAfterMs);
  }

  return new Promise((resolve) => {
    child.on('exit', (code) => {
      rmSync(root, { recursive: true, force: true });
      resolve({ code, stdout });
    });
  });
}

// A stub worker mirroring the bot's contract: on SIGTERM → graceful leave → exit 0.
// Without forwarding it would idle forever (and the old entrypoint would have needed SIGKILL).
const GRACEFUL_WORKER = `
process.on('SIGTERM', () => { console.log('WORKER-GRACEFUL-LEAVE'); process.exit(0); });
console.log('WORKER-READY');
setInterval(() => {}, 1000); // stay alive like a capturing bot
`;

const PLAIN_EXIT_WORKER = `
console.log('WORKER-READY');
process.exit(7);
`;

const main = async () => {
  {
    const r = await runEntrypoint(GRACEFUL_WORKER, { sigtermAfterMs: 50 });
    check('SIGTERM to the entrypoint is FORWARDED to the worker',
      r.stdout.includes('WORKER-GRACEFUL-LEAVE'), r.stdout.slice(-400));
    check('graceful leave → container exit 0 (not 137/143)', r.code === 0, `exit=${r.code}`);
    check('exit breadcrumb preserved', r.stdout.includes('worker exited with code 0'), r.stdout.slice(-400));
  }
  {
    const r = await runEntrypoint(PLAIN_EXIT_WORKER);
    check('a normal worker exit propagates its code unchanged', r.code === 7, `exit=${r.code}`);
    check('exit breadcrumb carries the code', r.stdout.includes('worker exited with code 7'), r.stdout.slice(-400));
  }
};

main().then(() => {
  if (failed) { console.error(`\n❌ entrypoint (L2): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ entrypoint (L2): PID-1 bash forwards TERM/INT to the worker and propagates its exit code — a stopped bot leaves gracefully instead of dying 137.');
});

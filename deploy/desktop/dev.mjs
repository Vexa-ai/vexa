#!/usr/bin/env node
/**
 * Vexa Desktop — hot dev orchestrator (deploy/desktop).
 *
 * Launches ALL of Vexa locally, hot, no Docker (VEXA-DESKTOP.md, option B: all-Node):
 *
 *   backend    modules/pipeline/scripts/desktop.ts   tsx watch  ── ingest :9099 + pipeline
 *                                                                  + delivery WS + recording tee
 *                                                                  + node:sqlite control plane :8056
 *   dashboard  services/dashboard                    next dev   ── :3001, NEXT_PUBLIC_API_URL → :8056
 *   extension  services/vexa-extension               esbuild w  ── rebuilds dist/ on edit
 *
 * Edit any layer → it reloads. One file DB (~/.vexa/desktop.db). STT is remote
 * (put TRANSCRIPTION_SERVICE_* in modules/pipeline/.env). This is the deployment
 * that doubles as the debug rig: modules → services → deploy, the local+hot cell.
 *
 *   cd deploy/desktop && npm run dev
 *   DESKTOP_NO_DASHBOARD=1 npm run dev   # backend + extension only
 */
import { spawn } from 'node:child_process';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const INGEST_PORT = process.env.INGEST_PORT || '9099';
const GATEWAY_PORT = process.env.GATEWAY_PORT || '8056';
const DASHBOARD_PORT = process.env.DASHBOARD_PORT || '3001';

const COLORS = { backend: '\x1b[36m', dashboard: '\x1b[35m', extension: '\x1b[33m', sys: '\x1b[32m' };
const log = (name, line) => process.stdout.write(`${COLORS[name] || ''}[${name}]\x1b[0m ${line}\n`);

const procs = [
  {
    name: 'backend', cwd: path.join(ROOT, 'modules', 'pipeline'),
    cmd: 'npx', args: ['tsx', 'watch', '--env-file-if-exists=.env', 'scripts/desktop.ts'],
    env: { INGEST_PORT, GATEWAY_PORT },
  },
];
if (!process.env.DESKTOP_NO_DASHBOARD) procs.push({
  name: 'dashboard', cwd: path.join(ROOT, 'services', 'dashboard'),
  cmd: 'npm', args: ['run', 'dev'],
  // point the real dashboard at the all-Node backend; single local user (no auth backend)
  env: {
    NEXT_PUBLIC_API_URL: `http://localhost:${GATEWAY_PORT}`,
    NEXT_PUBLIC_VEXA_API_URL: `http://localhost:${GATEWAY_PORT}`,
    PORT: DASHBOARD_PORT,
  },
});
if (!process.env.DESKTOP_NO_EXTENSION) procs.push({
  name: 'extension', cwd: path.join(ROOT, 'services', 'vexa-extension'),
  cmd: 'npm', args: ['run', 'dev'],
  env: {},
});

const children = [];
let shutting = false;

function start(p) {
  log('sys', `▶ ${p.name}: ${p.cmd} ${p.args.join(' ')}  (${path.relative(ROOT, p.cwd) || '.'})`);
  const child = spawn(p.cmd, p.args, {
    cwd: p.cwd,
    env: { ...process.env, ...p.env },
    shell: process.platform === 'win32', // npm/npx need the shell on Windows
    detached: process.platform !== 'win32', // own process group → we can reap the whole tree (tsx/next spawn children)
  });
  child.stdout.on('data', (d) => d.toString().split('\n').filter(Boolean).forEach((l) => log(p.name, l)));
  child.stderr.on('data', (d) => d.toString().split('\n').filter(Boolean).forEach((l) => log(p.name, l)));
  child.on('exit', (code) => {
    if (shutting) return;
    log('sys', `✗ ${p.name} exited (${code}) — shutting down the rest`);
    shutdown();
  });
  children.push(child);
}

function killTree(child, signal) {
  try {
    if (process.platform === 'win32') spawn('taskkill', ['/pid', String(child.pid), '/T', '/F']);
    else process.kill(-child.pid, signal); // negative pid → the whole process group (tsx/next + their children)
  } catch { /* already gone */ }
}
function shutdown() {
  if (shutting) return;
  shutting = true;
  for (const c of children) killTree(c, 'SIGTERM');
  setTimeout(() => { for (const c of children) killTree(c, 'SIGKILL'); process.exit(0); }, 2000);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

log('sys', `Vexa Desktop (hot) — ingest ws://localhost:${INGEST_PORT}/ingest · gateway http://localhost:${GATEWAY_PORT} · dashboard http://localhost:${DASHBOARD_PORT}`);
log('sys', `extension sidepanel → ingestUrl ws://localhost:${INGEST_PORT}/ingest · gatewayUrl http://localhost:${GATEWAY_PORT}`);
procs.forEach(start);

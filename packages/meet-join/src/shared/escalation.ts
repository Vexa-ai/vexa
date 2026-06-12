import { spawn } from 'child_process';
import { existsSync } from 'fs';
import { log, callNeedsHumanHelpCallback } from '../_host';

let escalationTriggered = false;
let vncStarted = false;

export interface EscalationResult {
  reason: string;
  urgency: 'high' | 'critical';
}

/**
 * Check if escalation should be triggered based on admission state.
 * Called from each platform's admission poll loop.
 * Returns non-null if escalation is needed.
 */
export function checkEscalation(
  elapsedMs: number,
  timeoutMs: number,
  unknownStateDurationMs: number,
  joinFailed?: boolean,
  pageAlive?: boolean
): EscalationResult | null {
  if (escalationTriggered) return null;

  if (elapsedMs > timeoutMs * 0.8) {
    return { reason: 'waiting_room_timeout_approaching', urgency: 'high' };
  }
  if (unknownStateDurationMs > 10_000) {
    return { reason: 'unknown_blocking_state', urgency: 'critical' };
  }
  if (joinFailed && pageAlive) {
    return { reason: 'join_error_page_alive', urgency: 'critical' };
  }
  return null;
}

/**
 * Trigger escalation: start VNC stack and notify meeting-api.
 * Idempotent — only fires once per admission attempt.
 */
export async function triggerEscalation(botConfig: any, reason: string): Promise<void> {
  if (escalationTriggered) return;
  escalationTriggered = true;

  log(`[Escalation] Triggered: ${reason}`);
  await startVncStack();
  await callNeedsHumanHelpCallback(botConfig, reason);
}

/**
 * Lazily start VNC stack on the existing Xvfb :99 display.
 * Meeting bots already render to :99 — this just exposes it.
 */
export async function startVncStack(): Promise<void> {
  if (vncStarted) return;

  // Platform guard: VNC exposes an X11 display. On a non-Linux host (a
  // contributor's macOS laptop) there is no Xvfb :99 — but the join layer there
  // runs HEADED Chromium, so a real window is already visible and VNC is moot.
  // Spawning x11vnc on macOS throws ENOENT and previously crashed the process
  // (weld-point #3 in the #439 isolation audit). Skip cleanly instead.
  const display = process.env.DISPLAY;
  if (process.platform !== 'linux' || !display) {
    log(`[Debug] VNC skipped (platform=${process.platform}, DISPLAY=${display || 'unset'}) — headed browser is directly visible; attach the agent over CDP instead.`);
    vncStarted = true; // mark done so we don't retry
    return;
  }

  log(`[Debug] Starting VNC stack on ${display}`);

  // spawn() failures surface as an async 'error' event; without a listener Node
  // throws and kills the bot. Attach a tolerant handler on each child.
  const safeSpawn = (cmd: string, args: string[]) => {
    try {
      const child = spawn(cmd, args, { stdio: 'ignore', detached: true });
      child.once('error', (e: any) => log(`[Debug] ${cmd} unavailable (${e?.code || e?.message}) — continuing`));
      child.unref();
    } catch (e: any) {
      log(`[Debug] ${cmd} spawn failed (${e?.message}) — continuing`);
    }
  };

  // x11vnc — expose existing Xvfb display
  safeSpawn('x11vnc', ['-display', display, '-forever', '-nopw', '-shared', '-rfbport', '5900']);

  // websockify — bridge VNC to WebSocket for noVNC
  const novncDir = '/usr/share/novnc';
  const wsArgs = existsSync(novncDir)
    ? ['--web', novncDir, '6080', 'localhost:5900']
    : ['6080', 'localhost:5900'];
  safeSpawn('websockify', wsArgs);

  // Wait for VNC port to be ready (up to 3s)
  await waitForPort(5900, 3000);
  vncStarted = true;
  log('[Debug] VNC stack started — port 5900 (VNC), 6080 (noVNC web)');
}

/**
 * Public, escalation-INDEPENDENT debug entry. This is the un-gating the joining
 * layer needs: a dev (or the agent) can turn on the live view for a HEALTHY
 * join, not only when the bot gets stuck. Pixels via VNC/noVNC (Linux), control
 * via CDP (any platform). Returns the endpoints so a runner can print them.
 */
export async function startDebugView(): Promise<{ vncUrl?: string; novncUrl?: string; cdpUrl: string }> {
  await startVncStack();
  const onLinux = process.platform === 'linux' && !!process.env.DISPLAY;
  return {
    vncUrl: onLinux ? 'vnc://localhost:5900' : undefined,
    novncUrl: onLinux ? 'http://localhost:6080/vnc.html' : undefined,
    cdpUrl: 'http://localhost:9222', // Playwright connectOverCDP target
  };
}

/**
 * Extra time (ms) granted when escalation is active, so the user has time to intervene.
 */
export function getEscalationExtensionMs(): number {
  return escalationTriggered ? 5 * 60 * 1000 : 0;
}

export function wasEscalationTriggered(): boolean {
  return escalationTriggered;
}

export function resetEscalation(): void {
  escalationTriggered = false;
}

// ---- internal helpers ----

function waitForPort(port: number, timeoutMs: number): Promise<void> {
  return new Promise((resolve) => {
    const start = Date.now();
    const check = () => {
      const net = require('net');
      const socket = new net.Socket();
      socket.setTimeout(200);
      socket.once('connect', () => {
        socket.destroy();
        resolve();
      });
      socket.once('error', () => {
        socket.destroy();
        if (Date.now() - start < timeoutMs) {
          setTimeout(check, 200);
        } else {
          log('[Escalation] VNC port wait timed out — continuing anyway');
          resolve();
        }
      });
      socket.once('timeout', () => {
        socket.destroy();
        if (Date.now() - start < timeoutMs) {
          setTimeout(check, 200);
        } else {
          resolve();
        }
      });
      socket.connect(port, '127.0.0.1');
    };
    check();
  });
}

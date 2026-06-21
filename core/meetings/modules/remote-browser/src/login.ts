/**
 * login — the "log in once, over VNC" provisioning flow.
 *
 * Opens a VNC-attachable persistent browser at the platform sign-in page and waits
 * for a human to authenticate, polling auth cookies *non-disruptively* (no navigation
 * away from the login page while they type). The moment a session cookie appears we
 * confirm with validateLoggedIn, optionally back the profile up, and close — which
 * flushes the persistent profile dir. That profile dir IS the saved session; a later
 * launchPersistentBrowser({dataDir: profileDir}) is authenticated.
 */
import { AuthPlatform, LoginStatus } from './types';
import { launchPersistentBrowser } from './browser';
import { getBrowserSessionArgs } from './args';
import { validateLoggedIn, AUTH_LOGIN_URLS } from './validate';
import { saveSessionLocal, cleanStaleLocks, ensureBrowserDataDir } from './session-store';

export interface ProvisionLoginOptions {
  platform: AuthPlatform;
  /** Persistent-context dir — the live session is written here. */
  profileDir: string;
  /** Optional durable copy of the auth-essential subset (e.g. a synced dir). */
  backupDir?: string;
  loginUrl?: string;
  /** Cookie poll interval (default 3s). */
  pollMs?: number;
  /** How long to wait for the human to finish logging in (default 5min). */
  timeoutMs?: number;
  /** Hold the browser open after detection so you can eyeball it in VNC. */
  keepOpenMs?: number;
}

export async function provisionLogin(opts: ProvisionLoginOptions): Promise<LoginStatus> {
  const pollMs = opts.pollMs ?? 3000;
  const timeoutMs = opts.timeoutMs ?? 300_000;
  const loginUrl = opts.loginUrl ?? AUTH_LOGIN_URLS[opts.platform];

  ensureBrowserDataDir(opts.profileDir);
  cleanStaleLocks(opts.profileDir);

  const { context, page } = await launchPersistentBrowser({ dataDir: opts.profileDir, args: getBrowserSessionArgs() });
  console.log(`[remote-browser] login: opened ${loginUrl} — sign in via VNC (:6080). Waiting up to ${Math.round(timeoutMs / 1000)}s...`);
  try { await page.goto(loginUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 }); } catch { /* keep polling regardless */ }

  // Detect login by the page LEAVING the sign-in / OAuth pages — a reliable,
  // non-disruptive signal — then confirm with validateLoggedIn. (Polling auth cookies
  // is unreliable: Zoom sets an anonymous _zm_ssid the instant the sign-in page loads,
  // which is NOT a logged-in signal and previously caused a false-positive early exit.)
  const onAuthPage = (u: string) =>
    !u || /about:blank|\/signin|\/login|accounts\.google\.com|login\.microsoftonline\.com|login\.live\.com/i.test(u);
  const deadline = Date.now() + timeoutMs;
  let status: LoginStatus = { loggedIn: false, detail: 'timed out waiting for sign-in' };
  while (Date.now() < deadline) {
    await page.waitForTimeout(pollMs);
    let u = ''; try { u = page.url(); } catch { /* navigating */ }
    if (onAuthPage(u)) continue;                            // still signing in — don't disturb the user
    status = await validateLoggedIn(page, opts.platform);   // left the login page — confirm for real
    if (status.loggedIn) break;
    // Off the login page but not yet confirmed (intermediate/redirect); validateLoggedIn
    // leaves us back on the sign-in page, so the next tick simply keeps waiting.
  }

  if (!status.loggedIn) {
    console.log(`[remote-browser] login: NOT CONFIRMED / timed out — ${status.detail}`);
    await context.close().catch(() => {});
    return status;
  }

  console.log('[remote-browser] login: confirmed logged in.');
  if (opts.backupDir) saveSessionLocal(opts.backupDir, opts.profileDir);
  if (opts.keepOpenMs) await page.waitForTimeout(opts.keepOpenMs);
  await context.close().catch(() => {}); // flush the persistent profile to disk
  console.log(`[remote-browser] login: SUCCESS — ${status.detail}`);
  return status;
}

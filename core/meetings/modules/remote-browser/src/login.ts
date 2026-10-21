/**
 * login — the "log in once, over VNC" provisioning flow.
 *
 * Opens a VNC-attachable persistent browser at the platform sign-in page and waits
 * for a human to authenticate, polling auth cookies *non-disruptively* (no navigation
 * away from the login page while they type). The moment a session cookie appears we
 * confirm with validateLoggedIn, optionally back the profile up, and close — which
 * flushes the persistent profile dir. That profile dir IS the saved session; a later
 * launchPersistentBrowser({dataDir: profileDir}) is authenticated.
 *
 * The person driving the browser over VNC types into its address bar, so the browser is confined
 * before it opens anything: every navigation (top level, popup, frame) off the platform's sign-in and
 * account domains (`SIGNIN_NAVIGATION_DOMAINS`) is aborted and logged — an internal service, a
 * `file://` path or a `chrome://` page included. Subresources still load from the platform's CDNs.
 * A company identity provider outside those domains (an Okta or ADFS tenant) is refused with them,
 * unless the operator names it (`extraHosts`, from `VEXA_BOT_SIGNIN_EXTRA_HOSTS`): an exact host or
 * `*.suffix`, over https only. An entry that is an IP literal, a bare label or localhost is dropped
 * here as well as where it is configured.
 */
import { AuthPlatform, LoginStatus } from './types';
import { launchPersistentBrowser } from './browser';
import { getBrowserSessionArgs } from './args';
import { validateLoggedIn, AUTH_LOGIN_URLS } from './validate';
import { AUTH_NAVIGATION_DOMAINS, extraHostEntry, restrictNavigation } from './navigation';

/** Where a sign-in browser may navigate: the platform's own domains, and for Zoom the Google and
 *  Microsoft account hosts its "sign in with" buttons hand over to. */
export const SIGNIN_NAVIGATION_DOMAINS: Record<AuthPlatform, readonly string[]> = {
  google: AUTH_NAVIGATION_DOMAINS.google,
  teams: AUTH_NAVIGATION_DOMAINS.teams,
  zoom: [...AUTH_NAVIGATION_DOMAINS.zoom, 'accounts.google.com', 'login.microsoftonline.com', 'login.live.com'],
};
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
  /** Launch flags. Default getBrowserSessionArgs() (VNC + CDP debug port). A caller that must expose
   *  nothing but its VNC bridge passes a set without CDP_DEBUG_ARGS. */
  args?: string[];
  /** The operator's extra sign-in hosts (exact or `*.suffix`, https only) — a company IdP. */
  extraHosts?: readonly string[];
  /** The launcher (default launchPersistentBrowser); a test passes a stand-in. */
  launch?: typeof launchPersistentBrowser;
}

export async function provisionLogin(opts: ProvisionLoginOptions): Promise<LoginStatus> {
  const pollMs = opts.pollMs ?? 3000;
  const timeoutMs = opts.timeoutMs ?? 300_000;
  const loginUrl = opts.loginUrl ?? AUTH_LOGIN_URLS[opts.platform];

  ensureBrowserDataDir(opts.profileDir);
  cleanStaleLocks(opts.profileDir);

  const launch = opts.launch ?? launchPersistentBrowser;
  const { context, page } = await launch({ dataDir: opts.profileDir, args: opts.args ?? getBrowserSessionArgs() });
  const extra = (opts.extraHosts ?? []).map(extraHostEntry).filter((e): e is string => e !== null);
  await restrictNavigation(context, SIGNIN_NAVIGATION_DOMAINS[opts.platform], undefined, extra);
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

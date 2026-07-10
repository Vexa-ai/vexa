/**
 * @vexa/remote-browser — the browser-as-container + session-persistence brick.
 *
 * One concern: a VNC/CDP-attachable *persistent* browser whose login session
 * (cookies / localStorage / Login Data) is saved and retrievable — so the join
 * layer can be handed an already-authenticated page (BotConfig.authenticated).
 *
 * Two flows:
 *   1. provisionLogin()  — start browser + VNC → human logs in → persist session.
 *   2. launchPersistentBrowser({dataDir}) + validateLoggedIn() — restore + confirm.
 *
 * Backends: S3 (syncBrowserData{To,From}S3 — production) or local (save/loadSessionLocal).
 * Carved from vexa-bot/core/src/{s3-sync.ts, browser-session.ts, constans.ts}; the bot
 * now imports these instead of re-declaring them (one-way rule: services import bricks).
 */

// Session store (persist / retrieve)
export {
  BROWSER_DATA_DIR,
  BROWSER_CACHE_EXCLUDES,
  s3Sync,
  syncBrowserDataFromS3,
  syncBrowserDataToS3,
  saveSessionLocal,
  loadSessionLocal,
  cleanStaleLocks,
  ensureBrowserDataDir,
  makeEphemeralProfileDir,
  removeProfileDir,
} from './session-store';
export type { S3Config } from './session-store';

// Launch flags (persistent-context / interactive)
export { getAuthenticatedBrowserArgs, getBrowserSessionArgs, CDP_DEBUG_ARGS } from './args';

// The one true persistent-context launch
export { launchPersistentBrowser } from './browser';
export type { LaunchPersistentOptions } from './browser';
// Re-export the Playwright handles this brick's API traffics in, so consumers (the bot
// composition root + its adapters) type against ONE Page/BrowserContext without a direct
// playwright dependency of their own.
export type { Page, BrowserContext } from 'playwright';

// Logged-in validation + login provisioning
export { validateLoggedIn, AUTH_LOGIN_URLS, AUTH_COOKIES } from './validate';
export { provisionLogin } from './login';
export type { ProvisionLoginOptions } from './login';

export type { AuthPlatform, LoginStatus } from './types';

/**
 * CLI: restore a saved profile and validate we're still logged in.
 *
 *   PLATFORM=zoom PROFILE=/data/profiles/myzoom tsx scripts/validate.ts
 *   PLATFORM=zoom PROFILE=/tmp/p RESTORE=/data/backups/myzoom tsx scripts/validate.ts
 *
 * Run inside the env image (so there's an X display). Exits 0 if logged in, 1 if not.
 */
import { launchPersistentBrowser } from '../src/browser';
import { getBrowserSessionArgs } from '../src/args';
import { validateLoggedIn } from '../src/validate';
import { loadSessionLocal, cleanStaleLocks, ensureBrowserDataDir } from '../src/session-store';
import { AuthPlatform } from '../src/types';

const platform = (process.env.PLATFORM || process.argv[2]) as AuthPlatform;
const profileDir = process.env.PROFILE || process.argv[3] || `/tmp/profiles/${platform}`;
const restoreFrom = process.env.RESTORE || undefined;

if (!platform || !['zoom', 'google', 'teams'].includes(platform)) {
  console.error('Usage: PLATFORM=zoom|google|teams PROFILE=<dir> [RESTORE=<backup-dir>] tsx scripts/validate.ts');
  process.exit(1);
}

(async () => {
  if (restoreFrom) loadSessionLocal(restoreFrom, profileDir);
  ensureBrowserDataDir(profileDir);
  cleanStaleLocks(profileDir);
  const { context, page } = await launchPersistentBrowser({ dataDir: profileDir, args: getBrowserSessionArgs() });
  const status = await validateLoggedIn(page, platform);
  console.log(`\n=== VALIDATE: loggedIn=${status.loggedIn} (${status.detail}) ===`);
  await context.close().catch(() => {});
  process.exit(status.loggedIn ? 0 : 1);
})();

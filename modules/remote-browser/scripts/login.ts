/**
 * CLI: log into a platform via VNC, persist the session.
 *
 *   PLATFORM=zoom PROFILE=/data/profiles/myzoom tsx scripts/login.ts
 *
 * Run inside the env image (Xvfb + noVNC on :6080). Open http://localhost:6080/vnc.html,
 * complete the sign-in; the moment a session cookie appears it confirms, saves, exits.
 * The PROFILE dir is the saved session — point validate.ts / the bot at the same dir.
 */
import { provisionLogin } from '../src/login';
import { AuthPlatform } from '../src/types';

const platform = (process.env.PLATFORM || process.argv[2]) as AuthPlatform;
const profileDir = process.env.PROFILE || process.argv[3] || `/tmp/profiles/${platform}`;
const backupDir = process.env.BACKUP || undefined;

if (!platform || !['zoom', 'google', 'teams'].includes(platform)) {
  console.error('Usage: PLATFORM=zoom|google|teams PROFILE=<dir> [BACKUP=<dir>] tsx scripts/login.ts');
  process.exit(1);
}

(async () => {
  const status = await provisionLogin({ platform, profileDir, backupDir, timeoutMs: 600_000, keepOpenMs: 5_000 });
  console.log(`\n=== LOGIN RESULT: loggedIn=${status.loggedIn} (${status.detail}) ===`);
  console.log(`profile saved at: ${profileDir}${backupDir ? ` (backup: ${backupDir})` : ''}`);
  process.exit(status.loggedIn ? 0 : 1);
})();

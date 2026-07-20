/**
 * provision-cli — the operator entry point that creates an authenticated bot session
 * (the `make login` / `pnpm --filter @vexa/remote-browser login` command, #724 C1).
 *
 * Flow: open the platform's sign-in page in the provisioning browser (headed locally, or
 * via the module's VNC flow inside a container) → a human signs in → `provisionLogin`
 * confirms with `validateLoggedIn` → the auth-essential subset of the profile is uploaded
 * to the deployment's userdata prefix (`syncBrowserDataToS3`). Every bot spawned by a
 * deployment configured with `BOT_AUTHENTICATED=true` then restores this session and
 * joins signed-in.
 *
 * Env (the SAME names meeting-api's spawn knob reads, one vocabulary per deployment):
 *   AUTH_PLATFORM            google | zoom | teams        (default: google)
 *   BOT_USERDATA_S3_PATH     userdata prefix, e.g. userdata/bot-identity-1
 *   BOT_S3_ENDPOINT          e.g. http://minio:9000
 *   BOT_S3_BUCKET            e.g. vexa
 *   BOT_S3_ACCESS_KEY / BOT_S3_SECRET_KEY   scoped userdata credentials — never admin creds
 *   LOGIN_PROFILE_DIR        where the live profile is written (default: BROWSER_DATA_DIR)
 *   LOGIN_TIMEOUT_MS         how long to wait for the human sign-in (default: 10 min)
 *
 * Exit codes: 0 = signed in AND (when S3 is configured) uploaded; 1 = login not confirmed
 * (the userdata prefix is untouched) or the upload shipped nothing.
 */
import { provisionLogin } from './login';
import { BROWSER_DATA_DIR, syncBrowserDataToS3, type S3Config } from './session-store';
import type { AuthPlatform } from './types';

const PLATFORMS: AuthPlatform[] = ['google', 'zoom', 'teams'];

async function main(): Promise<number> {
  const raw = (process.env.AUTH_PLATFORM || process.argv[2] || 'google').toLowerCase();
  if (!PLATFORMS.includes(raw as AuthPlatform)) {
    console.error(`[provision-login] unknown platform '${raw}' — expected one of: ${PLATFORMS.join(', ')}`);
    return 1;
  }
  const platform = raw as AuthPlatform;
  const profileDir = process.env.LOGIN_PROFILE_DIR || BROWSER_DATA_DIR;
  const timeoutMs = Number(process.env.LOGIN_TIMEOUT_MS) > 0 ? Number(process.env.LOGIN_TIMEOUT_MS) : 600_000;

  const s3: S3Config = {
    userdataS3Path: process.env.BOT_USERDATA_S3_PATH || undefined,
    s3Endpoint: process.env.BOT_S3_ENDPOINT || undefined,
    s3Bucket: process.env.BOT_S3_BUCKET || undefined,
    s3AccessKey: process.env.BOT_S3_ACCESS_KEY || undefined,
    s3SecretKey: process.env.BOT_S3_SECRET_KEY || undefined,
  };
  const s3Configured = !!(s3.userdataS3Path && s3.s3Endpoint && s3.s3Bucket);

  console.log(`[provision-login] platform=${platform} profileDir=${profileDir} ` +
    (s3Configured ? `upload → s3://${s3.s3Bucket}/${s3.userdataS3Path}` : 'no S3 config — profile dir only'));

  // The login gate: only a validateLoggedIn-confirmed session proceeds. An aborted /
  // timed-out sign-in exits non-zero with the login verdict and touches NOTHING durable.
  const status = await provisionLogin({ platform, profileDir, timeoutMs, keepOpenMs: 2000 });
  if (!status.loggedIn) {
    console.error(`[provision-login] FAILED — login not confirmed: ${status.detail}`);
    return 1;
  }

  if (!s3Configured) {
    console.log(`[provision-login] signed in; session lives in ${profileDir} (set BOT_USERDATA_S3_PATH ` +
      `+ BOT_S3_ENDPOINT + BOT_S3_BUCKET to persist it to the deployment's userdata storage).`);
    return 0;
  }

  const uploaded = syncBrowserDataToS3(s3, profileDir);
  if (uploaded === 0) {
    console.error('[provision-login] FAILED — signed in, but zero auth-essential items reached ' +
      `s3://${s3.s3Bucket}/${s3.userdataS3Path} (see warnings above; check credentials/endpoint/aws CLI).`);
    return 1;
  }
  console.log(`[provision-login] SUCCESS — ${uploaded} auth-essential items at ` +
    `s3://${s3.s3Bucket}/${s3.userdataS3Path}/browser-data; authenticated bots will restore this session.`);
  return 0;
}

main().then((code) => process.exit(code)).catch((e) => {
  console.error(`[provision-login] FAILED — ${String(e)}`);
  process.exit(1);
});

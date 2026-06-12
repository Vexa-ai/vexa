import { execSync } from 'child_process';
import { existsSync, unlinkSync, mkdirSync } from 'fs';
import { join } from 'path';

export const BROWSER_DATA_DIR = '/tmp/browser-data';

export const BROWSER_CACHE_EXCLUDES = [
  '*/Cache/*', '*/Code Cache/*', '*/GrShaderCache/*', '*/ShaderCache/*', '*/GraphiteDawnCache/*',
  '*/Service Worker/*', '*BrowserMetrics*',
  'SingletonLock', 'SingletonCookie', 'SingletonSocket',
  '*/GPUCache/*', '*/DawnGraphiteCache/*', '*/DawnWebGPUCache/*',
  '*/blob_storage/*', '*/File System/*', '*/IndexedDB/*',
];

export interface S3Config {
  userdataS3Path?: string;
  s3Endpoint?: string;
  s3Bucket?: string;
  s3AccessKey?: string;
  s3SecretKey?: string;
}

function getS3Env(config: S3Config): Record<string, string> {
  return {
    ...process.env as Record<string, string>,
    AWS_ACCESS_KEY_ID: config.s3AccessKey || '',
    AWS_SECRET_ACCESS_KEY: config.s3SecretKey || '',
  };
}

export function s3Sync(localDir: string, s3Path: string, config: S3Config, direction: 'up' | 'down', excludes: string[] = []): void {
  if (!config.userdataS3Path || !config.s3Endpoint || !config.s3Bucket) return;
  const s3Uri = `s3://${config.s3Bucket}/${s3Path}`;
  const excludeArgs = excludes.map(e => `--exclude "${e}"`).join(' ');
  const deleteArg = '';
  const [src, dst] = direction === 'down' ? [s3Uri, `${localDir}/`] : [`${localDir}/`, s3Uri];
  console.log(`[s3-sync] S3 sync ${direction}: ${src} → ${dst}`);
  execSync(
    `aws s3 sync "${src}" "${dst}" --endpoint-url "${config.s3Endpoint}" ${deleteArg} ${excludeArgs}`,
    { env: getS3Env(config), stdio: 'inherit', timeout: 300000 }
  );
}

export function syncBrowserDataFromS3(config: S3Config): void {
  s3Sync(BROWSER_DATA_DIR, `${config.userdataS3Path}/browser-data`, config, 'down', BROWSER_CACHE_EXCLUDES);
}

// Upload only auth-essential files via individual cp commands.
// ~200KB total, takes <2 seconds vs minutes for full sync.
const AUTH_ESSENTIAL_FILES = [
  'Local State',
  'Default/Cookies',
  'Default/Cookies-journal',
  'Default/Preferences',
  'Default/Secure Preferences',
  'Default/Login Data',
  'Default/Login Data-journal',
  'Default/Login Data For Account',
  'Default/Login Data For Account-journal',
  'Default/Network Persistent State',
  'Default/Web Data',
];

const AUTH_ESSENTIAL_DIRS = [
  'Default/Local Storage',
  'Default/Session Storage',
];

export function syncBrowserDataToS3(config: S3Config): void {
  if (!config.userdataS3Path || !config.s3Endpoint || !config.s3Bucket) return;
  const s3Base = `s3://${config.s3Bucket}/${config.userdataS3Path}/browser-data`;
  const env = getS3Env(config);
  const endpoint = `--endpoint-url "${config.s3Endpoint}"`;
  let uploaded = 0;

  console.log(`[s3-sync] S3 save (auth-essential files only)...`);

  for (const file of AUTH_ESSENTIAL_FILES) {
    const local = join(BROWSER_DATA_DIR, file);
    if (!existsSync(local)) continue;
    try {
      execSync(`aws s3 cp "${local}" "${s3Base}/${file}" ${endpoint}`, { env, stdio: 'pipe', timeout: 10000 });
      uploaded++;
    } catch (err: any) {
      console.log(`[s3-sync] Warning: failed to upload ${file}: ${err.message}`);
    }
  }

  for (const dir of AUTH_ESSENTIAL_DIRS) {
    const local = join(BROWSER_DATA_DIR, dir);
    if (!existsSync(local)) continue;
    try {
      execSync(`aws s3 sync "${local}/" "${s3Base}/${dir}/" ${endpoint}`, { env, stdio: 'pipe', timeout: 10000 });
      uploaded++;
    } catch (err: any) {
      console.log(`[s3-sync] Warning: failed to sync ${dir}: ${err.message}`);
    }
  }

  console.log(`[s3-sync] Uploaded ${uploaded} auth-essential items`);
}

export function cleanStaleLocks(dir: string = BROWSER_DATA_DIR): void {
  const lockFiles = ['SingletonLock', 'SingletonCookie', 'SingletonSocket'];
  for (const f of lockFiles) {
    const p = join(dir, f);
    if (existsSync(p)) {
      try { unlinkSync(p); } catch {}
      console.log(`[s3-sync] Removed stale lock: ${f}`);
    }
  }
}

export function ensureBrowserDataDir(): void {
  mkdirSync(BROWSER_DATA_DIR, { recursive: true });
}

/**
 * Upload a finalized raw-capture dir to the telemetry bucket under a
 * PARTITIONED prefix so captures are selectable by platform / date / meeting
 * with no database — the S3 prefix IS the index:
 *   telemetry/capture/v1/platform=<p>/date=<YYYY-MM-DD>/<meetingId>/
 * meta.json (written by RawCaptureService.finalize) carries num_speakers,
 * language, speakers[], etc. for finer selection (s3 select / listing + jq).
 * Best-effort: never throws into the shutdown path.
 */
export function uploadCaptureToS3(
  localDir: string,
  opts: { platform?: string; meetingId: string | number; bucket?: string; endpoint?: string; accessKey?: string; secretKey?: string },
): void {
  try {
    const bucket = opts.bucket || process.env.TELEMETRY_S3_BUCKET;
    const endpoint = opts.endpoint || process.env.TELEMETRY_S3_ENDPOINT || process.env.S3_ENDPOINT;
    if (!bucket) { console.log('[telemetry] TELEMETRY_S3_BUCKET unset — capture stays local'); return; }
    const platform = (opts.platform || 'unknown').replace(/[^a-z0-9_]+/gi, '-');
    const date = new Date().toISOString().slice(0, 10);
    const prefix = `telemetry/capture/v1/platform=${platform}/date=${date}/${opts.meetingId}`;
    const env = {
      ...process.env as Record<string, string>,
      AWS_ACCESS_KEY_ID: opts.accessKey || process.env.TELEMETRY_S3_ACCESS_KEY || process.env.AWS_ACCESS_KEY_ID || '',
      AWS_SECRET_ACCESS_KEY: opts.secretKey || process.env.TELEMETRY_S3_SECRET_KEY || process.env.AWS_SECRET_ACCESS_KEY || '',
    };
    const ep = endpoint ? `--endpoint-url "${endpoint}"` : '';
    execSync(`aws s3 sync "${localDir}/" "s3://${bucket}/${prefix}/" ${ep}`,
      { env, stdio: 'pipe', timeout: 300000 });
    console.log(`[telemetry] capture uploaded → s3://${bucket}/${prefix}/`);
  } catch (err: any) {
    console.log(`[telemetry] capture upload failed (non-fatal): ${err.message}`);
  }
}

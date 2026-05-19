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

import { existsSync, mkdirSync } from 'fs';
import { join } from 'path';
import { Readable } from 'stream';
import * as tar from 'tar';
import { cleanStaleLocks, BROWSER_DATA_DIR, AUTH_ESSENTIAL_FILES, AUTH_ESSENTIAL_DIRS } from './browser-profile';

export interface HttpCookieConfig {
  cookieServiceUrl: string;
  cookieServiceToken?: string;
  userId: string;
}

export async function verifyCookieServiceContract(config: HttpCookieConfig): Promise<void> {
  const health = await fetch(`${config.cookieServiceUrl}/health`, {
    signal: AbortSignal.timeout(5_000),
  });
  if (!health.ok) {
    throw new Error(`Cookie service unreachable at ${config.cookieServiceUrl}/health: ${health.status}`);
  }

  const headers: Record<string, string> = {};
  if (config.cookieServiceToken) {
    headers['Authorization'] = `Bearer ${config.cookieServiceToken}`;
  }
  const probe = await fetch(`${config.cookieServiceUrl}/userdata/__contract_probe__`, {
    headers,
    signal: AbortSignal.timeout(5_000),
  });
  if (probe.status === 401) {
    throw new Error(
      `Cookie service requires authentication (401). Check COOKIE_SERVICE_TOKEN configuration.`
    );
  }
  if (probe.status !== 200 && probe.status !== 404) {
    throw new Error(
      `Cookie service contract violation: GET /userdata returned ${probe.status}. ` +
      `Expected 200 or 404.`
    );
  }
}

export async function downloadCookiesFromHttp(config: HttpCookieConfig): Promise<void> {
  if (!config.userId) throw new Error('[cookie-http] userId is required');
  mkdirSync(BROWSER_DATA_DIR, { recursive: true });
  cleanStaleLocks(BROWSER_DATA_DIR);

  const headers: Record<string, string> = {};
  if (config.cookieServiceToken) {
    headers['Authorization'] = `Bearer ${config.cookieServiceToken}`;
  }

  const res = await fetch(`${config.cookieServiceUrl}/userdata/${config.userId}`, {
    headers,
    signal: AbortSignal.timeout(30_000),
  });

  if (res.status === 404) {
    console.log('[cookie-http] No cookies in store → anonymous join');
    return;
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`[cookie-http] Download failed: ${res.status} ${body}`);
  }

  const buf = Buffer.from(await res.arrayBuffer());
  await new Promise<void>((resolve, reject) => {
    Readable.from(buf)
      .pipe(tar.extract({ cwd: BROWSER_DATA_DIR, strict: false }))
      .on('finish', resolve)
      .on('error', reject);
  });

  console.log(`[cookie-http] Downloaded ${buf.length} bytes`);
}

export async function uploadCookiesToHttp(config: HttpCookieConfig): Promise<void> {
  if (!config.userId) throw new Error('[cookie-http] userId is required');
  const filesToPack = [
    ...AUTH_ESSENTIAL_FILES.filter(f => existsSync(join(BROWSER_DATA_DIR, f))),
    ...AUTH_ESSENTIAL_DIRS.filter(d => existsSync(join(BROWSER_DATA_DIR, d))),
  ];

  if (filesToPack.length === 0) {
    console.log('[cookie-http] Nothing to upload');
    return;
  }

  const chunks: Buffer[] = [];
  await new Promise<void>((resolve, reject) => {
    tar.create({ gzip: true, cwd: BROWSER_DATA_DIR, strict: false }, filesToPack)
      .on('data', (c: Buffer) => chunks.push(c))
      .on('end', resolve)
      .on('error', reject);
  });
  const body = Buffer.concat(chunks);

  const headers: Record<string, string> = {
    'Content-Type': 'application/octet-stream',
    'Content-Length': String(body.length),
  };
  if (config.cookieServiceToken) {
    headers['Authorization'] = `Bearer ${config.cookieServiceToken}`;
  }

  const res = await fetch(`${config.cookieServiceUrl}/userdata/${config.userId}`, {
    method: 'PUT',
    headers,
    body,
    signal: AbortSignal.timeout(30_000),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`[cookie-http] Upload failed: ${res.status} ${body}`);
  }
  console.log(`[cookie-http] Uploaded ${body.length} bytes`);
}

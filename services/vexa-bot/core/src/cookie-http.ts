import * as fs from 'fs';
import * as path from 'path';
import * as tar from 'tar';
import { BROWSER_DATA_DIR, cleanStaleLocks, ensureBrowserDataDir } from '@vexa/remote-browser';

export interface CookieHttpConfig {
  cookieServiceUrl: string;
  cookieServiceToken?: string;
  userId: string;
}

const AUTH_ESSENTIAL_FILES = [
  'Cookies',
  'Login Data',
  'Local State',
  'Preferences',
];

const AUTH_ESSENTIAL_DIRS = [
  'Default',
];

function authHeaders(token?: string): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function verifyCookieServiceContract(cfg: CookieHttpConfig): Promise<void> {
  const { cookieServiceUrl, cookieServiceToken } = cfg;
  const healthUrl = `${cookieServiceUrl}/health`;
  try {
    const r = await fetch(healthUrl, { headers: authHeaders(cookieServiceToken) });
    if (!r.ok) throw new Error(`Health check failed: ${r.status}`);
    const body = await r.json() as { status?: string };
    if (body.status !== 'ok') throw new Error(`Unexpected health body: ${JSON.stringify(body)}`);
  } catch (e) {
    console.error(`[cookie-http] Cookie service unreachable at ${healthUrl}: ${e}`);
    throw e;
  }
}

export async function downloadCookiesFromHttp(cfg: CookieHttpConfig): Promise<boolean> {
  const { cookieServiceUrl, cookieServiceToken, userId } = cfg;
  ensureBrowserDataDir();
  cleanStaleLocks(BROWSER_DATA_DIR);
  try {
    const r = await fetch(`${cookieServiceUrl}/userdata/${userId}`, {
      headers: authHeaders(cookieServiceToken),
    });
    if (r.status === 404) {
      console.log('[cookie-http] No stored cookies found (first run)');
      return false;
    }
    if (!r.ok) throw new Error(`Download failed: ${r.status}`);
    const buf = Buffer.from(await r.arrayBuffer());
    const tmpTar = path.join(BROWSER_DATA_DIR, '..', `_cookie_dl_${Date.now()}.tar.gz`);
    fs.writeFileSync(tmpTar, buf);
    await tar.extract({ file: tmpTar, cwd: path.dirname(BROWSER_DATA_DIR), strict: false });
    fs.unlinkSync(tmpTar);
    console.log('[cookie-http] Cookies restored from service');
    return true;
  } catch (e) {
    console.error(`[cookie-http] Failed to download cookies: ${e}`);
    return false;
  }
}

export async function uploadCookiesToHttp(cfg: CookieHttpConfig): Promise<void> {
  const { cookieServiceUrl, cookieServiceToken, userId } = cfg;
  const tmpTar = path.join(BROWSER_DATA_DIR, '..', `_cookie_ul_${Date.now()}.tar.gz`);
  try {
    const filesToPack: string[] = [];
    for (const f of AUTH_ESSENTIAL_FILES) {
      const full = path.join(BROWSER_DATA_DIR, 'Default', f);
      if (fs.existsSync(full)) filesToPack.push(path.join('Default', f));
    }
    for (const d of AUTH_ESSENTIAL_DIRS) {
      const full = path.join(BROWSER_DATA_DIR, d);
      if (fs.existsSync(full)) filesToPack.push(d);
    }
    if (filesToPack.length === 0) {
      console.log('[cookie-http] Nothing to upload');
      return;
    }
    await tar.create({ gzip: true, file: tmpTar, cwd: BROWSER_DATA_DIR }, filesToPack);
    const buf = fs.readFileSync(tmpTar);
    const r = await fetch(`${cookieServiceUrl}/userdata/${userId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/octet-stream', ...authHeaders(cookieServiceToken) },
      body: buf,
    });
    if (!r.ok) throw new Error(`Upload failed: ${r.status}`);
    console.log('[cookie-http] Cookies saved to service');
  } catch (e) {
    console.error(`[cookie-http] Failed to upload cookies: ${e}`);
  } finally {
    if (fs.existsSync(tmpTar)) fs.unlinkSync(tmpTar);
  }
}

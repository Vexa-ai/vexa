import * as fs from 'fs';
import * as path from 'path';
import { execSync } from 'child_process';
import { BROWSER_DATA_DIR, cleanStaleLocks, ensureBrowserDataDir } from '@vexa/remote-browser';

export interface CookieHttpConfig {
  cookieServiceUrl: string;
  cookieServiceToken?: string;
  userId: string;
}

const AUTH_ESSENTIAL = ['Default/Cookies', 'Default/Login Data', 'Default/Local State', 'Default/Preferences', 'Local State'];

function authHeaders(token?: string): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function verifyCookieServiceContract(cfg: CookieHttpConfig): Promise<void> {
  const r = await fetch(`${cfg.cookieServiceUrl}/health`, { headers: authHeaders(cfg.cookieServiceToken) });
  if (!r.ok) throw new Error(`Cookie service health check failed: ${r.status}`);
  const body = await r.json() as { status?: string };
  if (body.status !== 'ok') throw new Error(`Unexpected health response: ${JSON.stringify(body)}`);
}

export async function downloadCookiesFromHttp(cfg: CookieHttpConfig): Promise<boolean> {
  ensureBrowserDataDir();
  cleanStaleLocks(BROWSER_DATA_DIR);
  const r = await fetch(`${cfg.cookieServiceUrl}/userdata/${cfg.userId}`, {
    headers: authHeaders(cfg.cookieServiceToken),
  });
  if (r.status === 404) {
    console.log('[cookie-http] No stored cookies (first run)');
    return false;
  }
  if (!r.ok) throw new Error(`Cookie download failed: ${r.status}`);
  const buf = Buffer.from(await r.arrayBuffer());
  const tmp = path.join(BROWSER_DATA_DIR, '..', `_ck_dl_${Date.now()}.tar.gz`);
  fs.writeFileSync(tmp, buf);
  try {
    execSync(`tar -xzf "${tmp}" -C "${path.dirname(BROWSER_DATA_DIR)}"`, { stdio: 'pipe' });
    console.log('[cookie-http] Cookies restored');
    return true;
  } finally {
    if (fs.existsSync(tmp)) fs.unlinkSync(tmp);
  }
}

export async function uploadCookiesToHttp(cfg: CookieHttpConfig): Promise<void> {
  const tmp = path.join(BROWSER_DATA_DIR, '..', `_ck_ul_${Date.now()}.tar.gz`);
  try {
    const files = AUTH_ESSENTIAL.filter(f => fs.existsSync(path.join(BROWSER_DATA_DIR, f)));
    if (files.length === 0) { console.log('[cookie-http] Nothing to upload'); return; }
    execSync(`tar -czf "${tmp}" -C "${BROWSER_DATA_DIR}" ${files.map(f => `"${f}"`).join(' ')}`, { stdio: 'pipe' });
    const buf = fs.readFileSync(tmp);
    const r = await fetch(`${cfg.cookieServiceUrl}/userdata/${cfg.userId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/octet-stream', ...authHeaders(cfg.cookieServiceToken) },
      body: buf,
    });
    if (!r.ok) throw new Error(`Cookie upload failed: ${r.status}`);
    console.log('[cookie-http] Cookies saved');
  } catch (e) {
    console.error(`[cookie-http] Upload failed: ${e}`);
  } finally {
    if (fs.existsSync(tmp)) fs.unlinkSync(tmp);
  }
}

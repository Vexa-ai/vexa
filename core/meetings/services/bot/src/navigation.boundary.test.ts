/**
 * L3 boundary — an authenticated browser navigates only to the meeting's host and the platform's
 * domains (@vexa/remote-browser restrictNavigation), in real headless Chromium.
 *
 * Two hosts, both resolved to a local server by --host-resolver-rules: allowed.test (on the list,
 * standing in for the meeting's host) and blocked.test (off it). The browser is restricted to
 * allowed.test, then:
 *   1. a page on allowed.test loads, and its image from blocked.test still loads (a subresource is
 *      not a navigation);
 *   2. a navigation to blocked.test is refused before any request leaves (ERR_BLOCKED_BY_CLIENT);
 *   3. an iframe and a window.open to blocked.test never reach it;
 *   4. a redirect from allowed.test to blocked.test is caught where it lands: the frame ends on
 *      about:blank.
 * Where headless Chromium cannot launch the test SKIPS LOUDLY with exit 0, like the other boundary
 * tests. Run: npx tsx src/navigation.boundary.test.ts
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { launchPersistentBrowser, restrictNavigation, type BrowserContext } from '@vexa/remote-browser';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function main(): Promise<void> {
  const seen: string[] = [];
  const server = createServer((req: IncomingMessage, res: ServerResponse) => {
    const host = (req.headers.host || '').split(':')[0];
    seen.push(`${host}${req.url}`);
    const port = (server.address() as AddressInfo).port;
    if (req.url === '/redirect') {
      res.writeHead(302, { Location: `http://blocked.test:${port}/landed` });
      return res.end();
    }
    if (req.url === '/pixel.png') {
      res.writeHead(200, { 'Content-Type': 'image/png' });
      return res.end(Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
        + '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082', 'hex'));
    }
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(`<!doctype html><title>${host}${req.url}</title><body>${host}</body>`);
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const port = (server.address() as AddressInfo).port;
  const at = (host: string, path: string) => `http://${host}:${port}${path}`;

  const dataDir = mkdtempSync(join(tmpdir(), 'vexa-nav-boundary-'));
  let context: BrowserContext;
  let page;
  try {
    ({ context, page } = await launchPersistentBrowser({
      // site isolation on, as an authenticated bot runs: a cross-site frame is its own process
      dataDir, headless: true, args: ['--no-sandbox', '--site-per-process', '--host-resolver-rules=MAP *.test 127.0.0.1'],
    }));
  } catch (e) {
    console.log(`  ⚠️ SKIP — headless Chromium unavailable in this environment: ${(e as Error).message?.split('\n')[0]}`);
    server.close();
    process.exit(0);
  }
  const logs: string[] = [];
  try {
    await restrictNavigation(context, ['allowed.test', 'frames.test'], (l) => logs.push(l));

    // 1) an allowed page loads; its image from the other host still loads
    await page.goto(at('allowed.test', '/'));
    await page.evaluate((src: string) => new Promise<void>((resolve) => {
      const img = new Image(); img.onload = img.onerror = () => resolve(); img.src = src;
    }), at('blocked.test', '/pixel.png'));
    check('a page on the meeting host loads', page.url().startsWith(at('allowed.test', '/')), page.url());
    check('a subresource from another host still loads', seen.includes('blocked.test/pixel.png'), seen.join(' '));

    // 2) a navigation off the list is refused before it leaves
    let error = '';
    try { await page.goto(at('blocked.test', '/page')); } catch (e) { error = String((e as Error).message); }
    check('a navigation to another host is refused', /ERR_BLOCKED_BY_CLIENT/.test(error), error.split('\n')[0]);
    check('the refused navigation never reached the host', !seen.includes('blocked.test/page'));

    // 3) an iframe and a popup to the other host never reach it (a fresh page: the refused one is
    //    still settling onto Chromium's error page)
    page = await context.newPage();
    await page.goto(at('allowed.test', '/'));
    await page.evaluate((src: string) => {
      const f = document.createElement('iframe'); f.src = src; document.body.appendChild(f);
    }, at('blocked.test', '/inframe'));
    await page.evaluate((src: string) => { window.open(src); }, at('blocked.test', '/popup'));
    await page.evaluate((src: string) => {
      const f = document.createElement('iframe'); f.src = src; document.body.appendChild(f);
    }, at('frames.test', '/allowed-frame'));
    await sleep(1500);
    check('an iframe to another host never reaches it', !seen.includes('blocked.test/inframe'));
    check('a cross-site frame on the list loads in its own process', seen.includes('frames.test/allowed-frame')
      && page.frames().some((f) => f.url().includes('frames.test')));
    check('a popup to another host never reaches it', !seen.includes('blocked.test/popup'));

    // 4) a redirect that carries the page off the list is blanked where it lands
    await page.goto(at('allowed.test', '/redirect')).catch(() => { /* the guard may cut the load short */ });
    let url = page.url();
    for (let i = 0; i < 50 && url !== 'about:blank'; i++) { await sleep(100); url = page.url(); }
    check('a redirect off the list ends on about:blank', url === 'about:blank', url);
    check('each refusal is logged', logs.some((l) => l.includes('refused a navigation'))
      && logs.some((l) => l.includes('redirect carried a frame')), logs.join(' | '));
  } finally {
    await context.close().catch(() => {});
    rmSync(dataDir, { recursive: true, force: true });
    server.close();
  }
  if (failed) { console.log(`❌ navigation boundary: ${failed} failed`); process.exit(1); }
  console.log('✅ navigation boundary: an authenticated browser stays on its hosts (direct, frame, popup, redirect); subresources untouched');
  process.exit(0);
}

main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });

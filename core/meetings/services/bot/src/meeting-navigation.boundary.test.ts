/**
 * L3 boundary — every bot's browser stays on its meeting's hosts and on public addresses
 * (meeting-navigation.ts guardMeetingNavigation), in real headless Chromium.
 *
 * Chromium resolves every *.test name to a local server (--host-resolver-rules); the guard's own
 * resolver is a table, so a name can "resolve" privately for the guard while the browser would
 * reach it. The meeting is a Jitsi room on meet.org.test (an organisation's own host), then:
 *   1. the meeting page loads, and a same-host redirect is followed;
 *   2. a redirect to a host that resolves to the cloud metadata address is stopped BEFORE the
 *      browser sends the redirected request — the server never sees it — and the join's refusal
 *      reason is recorded (`meeting_host_refused`);
 *   3. the same for a redirect off the meeting's hosts, and for a two-hop chain whose second hop
 *      goes inside;
 *   4. a direct navigation and a subresource to an internal literal never leave the browser;
 *   5. a redirect to an operator-listed on-prem host (resolving privately) is followed.
 * Where headless Chromium cannot launch the test SKIPS LOUDLY with exit 0, like the other boundary
 * tests. Run: npx tsx src/meeting-navigation.boundary.test.ts
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { launchPersistentBrowser, type BrowserContext, type Page } from '@vexa/remote-browser';
import { guardMeetingNavigation, meetingNavigationPolicy, MEETING_HOST_REFUSED } from './meeting-navigation.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const DNS: Record<string, string[]> = {
  'meet.org.test': ['93.184.215.14'],
  'metadata.org.test': ['169.254.169.254'],
  'offlist.test': ['93.184.215.15'],
  'jitsi.corp.test': ['10.20.1.5'],
};
const resolver = async (host: string) => DNS[host] ?? [];

async function main(): Promise<void> {
  const seen: string[] = [];
  const server = createServer((req: IncomingMessage, res: ServerResponse) => {
    const host = (req.headers.host || '').split(':')[0];
    seen.push(`${host}${req.url}`);
    const port = (server.address() as AddressInfo).port;
    const go = (to: string) => { res.writeHead(302, { Location: to }); res.end(); };
    if (req.url === '/same') return go('/room');
    if (req.url === '/to-metadata') return go(`http://metadata.org.test:${port}/latest/meta-data`);
    if (req.url === '/to-offlist') return go(`http://offlist.test:${port}/phish`);
    if (req.url === '/hop1') return go(`http://meet.org.test:${port}/hop2`);
    if (req.url === '/hop2') return go(`http://metadata.org.test:${port}/second-hop`);
    if (req.url === '/to-onprem') return go(`http://jitsi.corp.test:${port}/room`);
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(`<!doctype html><title>${host}${req.url}</title><body>${host}</body>`);
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const port = (server.address() as AddressInfo).port;
  const at = (host: string, path: string) => `http://${host}:${port}${path}`;

  const dataDir = mkdtempSync(join(tmpdir(), 'vexa-meeting-nav-'));
  let context: BrowserContext;
  try {
    ({ context } = await launchPersistentBrowser({
      dataDir, headless: true, args: ['--no-sandbox', '--host-resolver-rules=MAP *.test 127.0.0.1'],
    }));
  } catch (e) {
    console.log(`  ⚠️ SKIP — headless Chromium unavailable in this environment: ${(e as Error).message?.split('\n')[0]}`);
    server.close();
    process.exit(0);
  }
  const logs: string[] = [];
  const goto = async (page: Page, url: string) => {
    try { await page.goto(url); return ''; } catch (e) { return String((e as Error).message).split('\n')[0]; }
  };
  try {
    const policy = meetingNavigationPolicy('jitsi', 'https://meet.org.test/room', ['jitsi.corp.test']);
    const guard = await guardMeetingNavigation(context, policy, { resolver, log: (l) => logs.push(l) });
    const page = context.pages()[0] ?? await context.newPage();

    // 1) the meeting page, and a redirect that stays on its host
    check('the meeting page loads', (await goto(page, at('meet.org.test', '/room'))) === '');
    await goto(page, at('meet.org.test', '/same'));
    check('a same-host redirect is followed', page.url() === at('meet.org.test', '/room'), page.url());
    check('nothing refused yet', guard.refusal() === null, String(guard.refusal()));

    // 2) a redirect to a name that resolves to cloud metadata
    const err = await goto(page, at('meet.org.test', '/to-metadata'));
    check('a redirect to an internal-resolving host is aborted', /ERR_BLOCKED_BY_CLIENT/.test(err), err);
    check('…before the browser sent the redirected request', !seen.some((s) => s.startsWith('metadata.org.test')), seen.join(' '));
    check('…and the join has a typed refusal', (guard.refusal() ?? '').startsWith(`${MEETING_HOST_REFUSED}: navigation failed`), String(guard.refusal()));

    // 3) off the meeting's hosts, and a two-hop chain whose second hop goes inside
    const err2 = await goto(page, at('meet.org.test', '/to-offlist'));
    check('a redirect off the meeting hosts is aborted', /ERR_BLOCKED_BY_CLIENT/.test(err2) && !seen.some((s) => s.startsWith('offlist.test')), `${err2} ${seen.join(' ')}`);
    const err3 = await goto(page, at('meet.org.test', '/hop1'));
    check('the second hop of a redirect chain is checked too', /ERR_BLOCKED_BY_CLIENT/.test(err3)
      && seen.includes('meet.org.test/hop2') && !seen.includes('metadata.org.test/second-hop'), `${err3} ${seen.join(' ')}`);

    // 4) a direct navigation and a subresource to an internal literal
    const err4 = await goto(page, 'http://169.254.169.254/latest/meta-data');
    check('a direct navigation to the metadata address is aborted', /ERR_BLOCKED_BY_CLIENT/.test(err4), err4);
    // (a fresh page: the refused one is still settling onto Chromium's error page)
    const page2 = await context.newPage();
    await goto(page2, at('meet.org.test', '/room'));
    await page2.evaluate((src: string) => new Promise<void>((resolve) => {
      const img = new Image(); img.onload = img.onerror = () => resolve(); img.src = src;
    }), `http://127.0.0.1:${port}/pixel.png`);
    check('a subresource to an internal literal never leaves', !seen.some((s) => s.startsWith('127.0.0.1')), seen.join(' '));

    // 5) a redirect to the operator's listed on-prem host
    await goto(page2, at('meet.org.test', '/to-onprem'));
    check('a redirect to a listed on-prem host is followed', page2.url() === at('jitsi.corp.test', '/room'), page2.url());
    check('each refusal is logged', logs.filter((l) => l.includes(MEETING_HOST_REFUSED)).length >= 4, logs.join(' | '));
  } finally {
    await context.close().catch(() => {});
    rmSync(dataDir, { recursive: true, force: true });
    server.close();
  }
  if (failed) { console.log(`❌ meeting navigation boundary: ${failed} failed`); process.exit(1); }
  console.log('✅ meeting navigation boundary: redirects and navigations inside or off the meeting hosts never leave the browser');
  process.exit(0);
}

main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });

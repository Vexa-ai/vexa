/**
 * navigation — an authenticated browser navigates only to the meeting's host and the platform's own
 * domains. Pure logic plus restrictNavigation over a stand-in context; no Chromium (the real-browser
 * proof is the bot's navigation.boundary.test.ts). Same shape as auth.smoke.test.ts.
 */
import {
  AUTH_NAVIGATION_DOMAINS, authenticatedNavigationDomains, hostAllowed, offList, restrictNavigation,
} from './navigation';

const fails: string[] = [];
const check = (cond: boolean, msg: string) => { if (!cond) fails.push(msg); };

// ── the domain rule ──────────────────────────────────────────────────────────
const google = AUTH_NAVIGATION_DOMAINS.google;
check(hostAllowed('meet.google.com', google) && hostAllowed('accounts.google.com', google), 'google subdomains allowed');
check(hostAllowed('google.com', google) && hostAllowed('MEET.GOOGLE.COM.', google), 'the domain itself, any case, trailing dot');
for (const evil of ['evilgoogle.com', 'google.com.evil.net', 'meet-google.com', 'google.co', 'example.com', '']) {
  check(!hostAllowed(evil, google), `${evil || '(empty)'} is not a google domain`);
}
check(hostAllowed('teams.microsoft.com', AUTH_NAVIGATION_DOMAINS.teams)
  && hostAllowed('login.microsoftonline.com', AUTH_NAVIGATION_DOMAINS.teams), 'teams meeting and sign-in hosts');
check(hostAllowed('us02web.zoom.us', AUTH_NAVIGATION_DOMAINS.zoom), 'zoom web client host');

const meet = authenticatedNavigationDomains('google', 'https://meet.google.com/abc-defg-hij');
check(meet.includes('google.com') && meet.includes('meet.google.com'), 'google meeting: platform domains + meeting host');
const jitsi = authenticatedNavigationDomains(null, 'https://jitsi.example.org/room');
check(jitsi.length === 1 && jitsi[0] === 'jitsi.example.org', 'no platform: only the meeting host');
check(authenticatedNavigationDomains(null, 'not a url').length === 0, 'an unparsable meeting URL adds nothing');

check(!offList(new URL('about:blank'), meet) && !offList(new URL('data:text/html,x'), meet), 'blank and data frames pass');
check(offList(new URL('https://attacker.example/'), meet), 'another host is off the list');

// ── restrictNavigation over a stand-in context ───────────────────────────────
async function routed() {
  let matcher: ((u: URL) => boolean) | null = null;
  let handler: ((route: any) => Promise<void>) | null = null;
  const pageHandlers: ((frame: any) => void)[] = [];
  const page = { on: (ev: string, fn: any) => { if (ev === 'framenavigated') pageHandlers.push(fn); } };
  const context: any = {
    route: async (m: any, h: any) => { matcher = m; handler = h; },
    pages: () => [page],
    on: () => {},
  };
  const logs: string[] = [];
  await restrictNavigation(context, meet, (l) => logs.push(l));
  check(!!matcher && !!handler, 'a route is installed');
  check(matcher!(new URL('https://meet.google.com/x')) === false, 'the meeting host is not intercepted');
  check(matcher!(new URL('https://attacker.example/')) === true, 'an off-list host is intercepted');

  const outcome = async (isNav: boolean) => {
    let did = '';
    const route = {
      request: () => ({ isNavigationRequest: () => isNav, url: () => 'https://attacker.example/x' }),
      abort: async (code: string) => { did = `abort:${code}`; },
      continue: async () => { did = 'continue'; },
    };
    await handler!(route);
    return did;
  };
  check(await outcome(true) === 'abort:blockedbyclient', 'an off-list navigation is aborted');
  check(await outcome(false) === 'continue', 'an off-list subresource goes through');
  check(logs.some((l) => l.includes('attacker.example')), 'the refusal is logged with the host');

  // a redirect that lands off the list: that frame is blanked
  const gone: string[] = [];
  const frame = (url: string) => ({ url: () => url, goto: async (to: string) => { gone.push(`${url}->${to}`); } });
  pageHandlers.forEach((fn) => fn(frame('https://attacker.example/landed')));
  pageHandlers.forEach((fn) => fn(frame('https://meet.google.com/abc')));
  pageHandlers.forEach((fn) => fn(frame('chrome-error://chromewebdata/')));
  check(gone.length === 1 && gone[0] === 'https://attacker.example/landed->about:blank',
    `only the off-list landing is blanked (${gone.join(', ')})`);
}

async function main() {
  await routed();
  if (fails.length) {
    console.log('❌ FAIL —\n  ' + fails.join('\n  '));
    process.exit(1);
  }
  console.log('✅ PASS — an authenticated browser navigates only to the meeting host and the platform domains; off-list navigations are aborted, redirect landings blanked, subresources untouched.');
  process.exit(0);
}

main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });

/**
 * login — the sign-in browser a person drives over VNC is confined to the platform's sign-in hosts
 * BEFORE it opens anything: an internal service, a private address, `file://` and `chrome://` are
 * aborted as navigations. A stand-in launcher; no Chromium. Same shape as navigation.test.ts.
 */
import { provisionLogin, SIGNIN_NAVIGATION_DOMAINS } from './login';

const fails: string[] = [];
const check = (cond: boolean, msg: string) => { if (!cond) fails.push(msg); };

async function confined(platform: 'google' | 'zoom' | 'teams', extraHosts?: string[]) {
  const order: string[] = [];
  let matcher: ((u: URL) => boolean) | null = null;
  let handler: ((route: any) => Promise<void>) | null = null;
  const page: any = {
    on: () => {},
    goto: async (url: string) => { order.push(`goto:${url}`); },
    url: () => 'about:blank',
    waitForTimeout: async () => {},
  };
  const context: any = {
    route: async (m: any, h: any) => { order.push('route'); matcher = m; handler = h; },
    pages: () => [page],
    on: () => {},
    close: async () => {},
  };
  const launch: any = async () => ({ context, page });
  const status = await provisionLogin({ platform, profileDir: `/tmp/vexa-login-test-${process.pid}-${platform}`,
    timeoutMs: 1, pollMs: 0, launch, extraHosts });
  check(!status.loggedIn, `${platform}: nothing signed in with a stand-in`);
  check(order[0] === 'route' && order[1]?.startsWith('goto:'), `${platform}: confined before the first navigation (${order.join(', ')})`);
  check(!!matcher && !!handler, `${platform}: a route is installed`);
  for (const internal of ['http://meeting-api:8080/meetings', 'http://10.0.0.5/', 'http://169.254.169.254/latest/meta-data/',
    'http://minio:9000/', 'file:///proc/self/environ', 'chrome://settings/', 'https://attacker.example/']) {
    check(matcher!(new URL(internal)) === true, `${platform}: ${internal} is intercepted`);
  }
  let did = '';
  await handler!({ request: () => ({ isNavigationRequest: () => true, url: () => 'http://meeting-api:8080/' }),
    abort: async (c: string) => { did = `abort:${c}`; }, continue: async () => { did = 'continue'; } });
  check(did === 'abort:blockedbyclient', `${platform}: typing an internal URL is aborted (${did})`);
  return matcher!;
}

async function main() {
  const google = await confined('google');
  check(google(new URL('https://accounts.google.com/signin')) === false, 'google: the sign-in host passes');
  const zoom = await confined('zoom');
  check(zoom(new URL('https://zoom.us/signin')) === false && zoom(new URL('https://accounts.google.com/o/oauth2')) === false,
    'zoom: its own sign-in and the Google hand-over pass');
  check(zoom(new URL('https://mail.google.com/')) === true, 'zoom: only the Google ACCOUNT host, not every google.com host');
  const teams = await confined('teams');
  check(teams(new URL('https://login.microsoftonline.com/common/oauth2')) === false, 'teams: the Microsoft sign-in host passes');
  check(SIGNIN_NAVIGATION_DOMAINS.google.length > 0, 'every platform has sign-in hosts');
  check(teams(new URL('https://adfs.bank.example/adfs/ls')) === true, 'teams: an unlisted company IdP is refused');

  // The operator's extra sign-in hosts (VEXA_BOT_SIGNIN_EXTRA_HOSTS): a bank's own ADFS.
  const bank = await confined('teams', ['adfs.bank.example', '*.okta.bank.example', '10.0.0.5', '192.168.1.1',
    'intranet', 'localhost', '*.localhost', '*.com', '[::1]', '169.254.169.254']);
  check(bank(new URL('https://adfs.bank.example/adfs/ls')) === false, 'an extra exact host is allowed');
  check(bank(new URL('https://x.adfs.bank.example/')) === true, 'an exact entry does not admit its subdomains');
  check(bank(new URL('https://tenant.okta.bank.example/')) === false, 'a *.suffix entry admits its subdomains');
  check(bank(new URL('https://okta.bank.example/')) === true, 'a *.suffix entry does not admit the suffix itself');
  check(bank(new URL('http://adfs.bank.example/')) === true, 'an extra host is https only');
  check(bank(new URL('https://idp.other.example/')) === true, 'an unlisted IdP is still refused');
  for (const internal of ['http://10.0.0.5/', 'https://10.0.0.5/', 'https://192.168.1.1/', 'https://intranet/',
    'https://localhost/', 'https://a.localhost/', 'https://example.com/', 'https://[::1]/', 'https://169.254.169.254/']) {
    check(bank(new URL(internal)) === true, `an extra entry may not admit ${internal}`);
  }
  check(bank(new URL('https://login.microsoftonline.com/')) === false, 'the platform hosts stay allowed beside the extras');
  if (fails.length) {
    console.log('❌ FAIL —\n  ' + fails.join('\n  '));
    process.exit(1);
  }
  console.log('✅ PASS — the sign-in browser is confined to the platform sign-in hosts before it opens anything.');
  process.exit(0);
}

main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });

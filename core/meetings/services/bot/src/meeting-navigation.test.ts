/**
 * L2 — the meeting navigation rule (meeting-navigation.ts), offline: the policy per platform, the
 * name and address checks, the launch-time pin of an organisation's own meeting host, and the join
 * failure a refused navigation becomes. DNS is a table. The real-Chromium half (a redirect stopped
 * before it leaves) is meeting-navigation.boundary.test.ts.
 *
 * Run: tsx src/meeting-navigation.test.ts
 */
import { classifyJoinFailure } from './join-evidence.js';
import {
  MEETING_HOST_REFUSED, MeetingHostRefused, checkNavigation, checkWithoutLookup, meetingNavigationPolicy,
  pinMeetingHostArgs, withNavigationRefusal, type NavigationGuard,
} from './meeting-navigation.js';
import type { JoinDriver, JoinResult } from './ports.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const PUBLIC = '93.184.215.14';
const DNS: Record<string, string[]> = {
  'rebound.example': ['169.254.169.254'],
  'inside.zoom.us': ['10.0.0.7'],
  'jitsi.corp.example': ['10.20.1.5'],
  'meta.corp.example': ['169.254.169.254'],
  'other.corp.example': ['10.30.0.4'],
  'mixed.example': [PUBLIC, '192.168.1.1'],
};
const resolver = async (host: string) => DNS[host] ?? [PUBLIC];
const u = (s: string) => new URL(s);

async function main(): Promise<void> {
  // ── the policy ──
  const meet = meetingNavigationPolicy('google_meet', 'https://meet.google.com/abc-defg-hij');
  check('Meet: platform domains only, no custom host', meet.customHost === null && meet.domains.includes('google.com'));
  const jitsi = meetingNavigationPolicy('jitsi', 'https://meet.example.org/room');
  check('Jitsi on an organisation host: that host joins the list and is the custom host',
    jitsi.customHost === 'meet.example.org' && jitsi.domains.includes('meet.example.org'));
  const zoomVanity = meetingNavigationPolicy('zoom', 'https://acme.zoom.us/j/81234567890');
  check('Zoom vanity subdomain is on the platform', zoomVanity.customHost === null);
  let threw = '';
  try { meetingNavigationPolicy('jitsi', 'http://meet.example.org/room'); } catch (e) { threw = String(e); }
  check('a non-https meeting URL is refused', threw.includes(MEETING_HOST_REFUSED), threw);
  threw = '';
  try { meetingNavigationPolicy('jitsi', 'https://x.example/r', ['169.254.0.0/16']); } catch (e) { threw = String(e); }
  check('an allowance overlapping metadata is refused', threw.includes(MEETING_HOST_REFUSED), threw);
  check('no meeting URL: the platform domains only',
    meetingNavigationPolicy('zoom', null).customHost === null);

  // ── checks without a lookup ──
  const teams = meetingNavigationPolicy('teams', 'https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%40thread.v2/0');
  check('Teams launcher and sign-in hosts stay on the list',
    checkWithoutLookup(u('https://teams.live.com/dl/launcher/launcher.html?url=x'), teams, true).ok
    && checkWithoutLookup(u('https://login.microsoftonline.com/common/oauth2'), teams, true).ok);
  check('Meet sign-in (accounts.google.com) stays on the list',
    checkWithoutLookup(u('https://accounts.google.com/ServiceLogin'), meet, true).ok);
  check('Zoom web client redirect (app.zoom.us/wc) stays on the list',
    checkWithoutLookup(u('https://app.zoom.us/wc/join/81234567890'), zoomVanity, true).ok);
  const off = checkWithoutLookup(u('https://evil.example/x'), meet, true);
  check('a top-level navigation off the platform is refused', !off.ok && off.why.includes('not a google_meet host'));
  check('a subresource off the platform is not', checkWithoutLookup(u('https://cdn.example/x.js'), meet, false).ok);
  for (const bad of ['http://169.254.169.254/latest/meta-data', 'http://10.0.0.5/', 'http://127.0.0.1:8080/',
    'http://[::ffff:169.254.169.254]/', 'http://2130706433/', 'http://redis:6379/', 'http://localhost/',
    'http://metadata.google.internal/']) {
    check(`any request to ${bad} is refused`, !checkWithoutLookup(u(bad), meet, false).ok);
  }

  // ── with a lookup ──
  const rebound = await checkNavigation(u('https://rebound.example/x'), jitsi, false, resolver);
  check('a frame navigation to a name resolving to metadata is refused', !rebound.ok);
  const zoomInside = await checkNavigation(u('https://inside.zoom.us/j/1'), zoomVanity, true, resolver);
  check('a platform host that resolves inside is refused', !zoomInside.ok);
  check('mixed public and private answers are refused',
    !(await checkNavigation(u('https://mixed.example/'), jitsi, false, resolver)).ok);
  check('a public platform host is admitted', (await checkNavigation(u('https://app.zoom.us/wc/1'), zoomVanity, true, resolver)).ok);

  // ── the operator's allowance ──
  const onprem = meetingNavigationPolicy('jitsi', 'https://jitsi.corp.example/room', ['jitsi.corp.example']);
  check('a listed on-prem Jitsi resolving privately is admitted',
    (await checkNavigation(u('https://jitsi.corp.example/room'), onprem, true, resolver)).ok);
  check('another internal host is still refused',
    !(await checkNavigation(u('https://other.corp.example/'), onprem, false, resolver)).ok);
  const metaListed = meetingNavigationPolicy('jitsi', 'https://meta.corp.example/room', ['meta.corp.example']);
  check('a listed name resolving to metadata is still refused',
    !(await checkNavigation(u('https://meta.corp.example/'), metaListed, true, resolver)).ok);
  const net = meetingNavigationPolicy('jitsi', 'https://jitsi.corp.example/room', ['10.20.0.0/16']);
  check('a listed network admits its addresses',
    (await checkNavigation(u('https://jitsi.corp.example/room'), net, true, resolver)).ok
    && checkWithoutLookup(u('https://10.20.1.5/room'), net, true).ok);

  // ── the launch pin ──
  check('an organisation host is pinned to its checked address',
    JSON.stringify(await pinMeetingHostArgs(jitsi, resolver)) === JSON.stringify([`--host-resolver-rules=MAP meet.example.org ${PUBLIC}`]));
  check('a platform host is not pinned', (await pinMeetingHostArgs(meet, resolver)).length === 0);
  check('a listed on-prem host is pinned to its private address',
    (await pinMeetingHostArgs(onprem, resolver))[0] === '--host-resolver-rules=MAP jitsi.corp.example 10.20.1.5');
  threw = '';
  try { await pinMeetingHostArgs(meetingNavigationPolicy('jitsi', 'https://rebound.example/r'), resolver); }
  catch (e) { threw = e instanceof MeetingHostRefused ? e.message : String(e); }
  check('an organisation host resolving inside refuses the launch', threw.startsWith(MEETING_HOST_REFUSED), threw);

  // ── the join failure ──
  const refusedGuard: NavigationGuard = { refusal: () => `${MEETING_HOST_REFUSED}: navigation failed — a redirect to rebound.example refused: resolves to an internal address (169.254.169.254)` };
  const quietGuard: NavigationGuard = { refusal: () => null };
  const driver = (join: JoinDriver['join']): JoinDriver => ({
    join, onRemoval: () => () => {}, leave: async () => {}, withdraw: async () => {},
  });
  const thrown = await withNavigationRefusal(driver(async () => { throw new Error('net::ERR_BLOCKED_BY_CLIENT'); }), refusedGuard)
    .join(() => {}) as JoinResult;
  check('a refused navigation becomes a typed join failure', thrown.outcome === 'error'
    && (thrown.reason ?? '').startsWith(MEETING_HOST_REFUSED), JSON.stringify(thrown));
  check('…classified as a navigation failure',
    classifyJoinFailure('error', { detail: thrown.reason }) === 'navigation_failure');
  const rejected = await withNavigationRefusal(driver(async () => ({ outcome: 'rejected' })), refusedGuard).join(() => {}) as JoinResult;
  check('a non-admitted verdict after a refusal carries the refusal', (rejected.reason ?? '').startsWith(MEETING_HOST_REFUSED));
  check('an admitted join is untouched', (await withNavigationRefusal(driver(async () => 'admitted'), refusedGuard).join(() => {})) === 'admitted');
  let rethrown = '';
  try { await withNavigationRefusal(driver(async () => { throw new Error('boom'); }), quietGuard).join(() => {}); }
  catch (e) { rethrown = String(e); }
  check('without a refusal a throw stays a throw', rethrown.includes('boom'));

  if (failed) { console.log(`❌ meeting navigation: ${failed} failed`); process.exit(1); }
  console.log('✅ meeting navigation: platform hosts, public addresses, operator allowance, launch pin, typed join failure');
}

main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });

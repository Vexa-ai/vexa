/**
 * meeting-navigation — where the bot's browser may go, for every bot (guest or signed in).
 *
 * The meeting URL is chosen by whoever sent the bot. meeting-api holds it to the meeting host rule
 * before the spawn (`bot_spawn/meeting_host.py`): the host is the platform's (or, for Jitsi and a
 * Zoom front door, an organisation's own) and resolves to a public or operator-listed address. A
 * browser, though, follows redirects and resolves names again on its own. So the browser holds every
 * navigation to the same rule:
 *
 *  - a TOP-LEVEL navigation (the page, or a popup) stays on the platform's domains or the meeting's
 *    own host (`NAVIGATION_DOMAINS`, the meeting URL's host, the operator's listed hosts);
 *  - ANY navigation (frames too) reaches a public address: the host is resolved and every address
 *    checked by the outbound URL guard (`@vexa/transcribe-whisper` url-guard, the TypeScript twin of
 *    outbound-url.v1), unless the operator's `meetingHostAllow` (invocation.v1) admits it;
 *  - ANY request to an internal IP literal or a deployment-only name (`localhost`, a metadata name,
 *    a single label) is refused outright — no lookup needed.
 *
 * Three layers, because each sees a different part of a navigation:
 *  1. request interception (`context.route`) sees the first request of each navigation and every
 *     subresource, and aborts before anything leaves the browser;
 *  2. a CDP `Fetch` handler at the RESPONSE stage sees each redirect hop's 3xx and its `Location`
 *     before Chromium follows it (Playwright continues redirect hops without routing them), and
 *     fails the hop when the target is refused;
 *  3. the meeting's own host, when it is not a platform domain, is resolved and checked at launch
 *     and pinned in Chromium's resolver (`--host-resolver-rules`), so a record that flips later
 *     (DNS rebinding) still reaches the checked address.
 * A top-level refusal is recorded; the join then fails with a `meeting_host_refused` reason.
 */
import { AUTH_NAVIGATION_DOMAINS, hostAllowed, type BrowserContext, type Page } from '@vexa/remote-browser';
import {
  allowanceAdmitsHost, allowanceAdmitsIp, isBlockedHostname, isBlockedIp, literalAddress, parseAllowance,
  resolveHost, type OperatorAllowance, type Resolver,
} from '@vexa/transcribe-whisper';
import type { JoinDriver, JoinOutcome, JoinResult } from './ports.js';

/** The reason code a refused navigation's join failure starts with. */
export const MEETING_HOST_REFUSED = 'meeting_host_refused';

/** The domains a meeting's top-level navigations may reach, per platform (a domain also covers its
 *  subdomains): the meeting's platform and its own sign-in and launcher flows, nothing else. */
export const NAVIGATION_DOMAINS: Record<string, readonly string[]> = {
  google_meet: AUTH_NAVIGATION_DOMAINS.google,
  teams: AUTH_NAVIGATION_DOMAINS.teams,
  zoom: AUTH_NAVIGATION_DOMAINS.zoom,
  jitsi: ['meet.jit.si'],
};

/** Schemes a navigation may use without naming a host (blank frames, blobs, Chromium's own pages). */
const HOSTLESS_SCHEMES = new Set(['about:', 'data:', 'blob:', 'chrome-error:']);

export class MeetingHostRefused extends Error {
  constructor(message: string) { super(`${MEETING_HOST_REFUSED}: ${message}`); this.name = 'MeetingHostRefused'; }
}

export interface NavigationPolicy {
  platform: string;
  /** Platform domains plus the meeting's own host (lowercase). */
  domains: readonly string[];
  /** The meeting URL's host, when it is not on a platform domain (an organisation's own host). */
  customHost: string | null;
  allow: OperatorAllowance | null;
}

export type Verdict = { ok: true } | { ok: false; host: string; why: string };
const OK: Verdict = { ok: true };

/** The policy for one meeting: the platform's domains, the meeting URL's host and the operator's
 *  allowance (invocation.v1 `meetingHostAllow`). Throws MeetingHostRefused for a meeting URL the bot
 *  must not open at all (unparseable, not https). No meeting URL: the platform's domains only. */
export function meetingNavigationPolicy(platform: string, meetingUrl: string | null, allowEntries?: readonly string[]): NavigationPolicy {
  const platformDomains = NAVIGATION_DOMAINS[platform] ?? [];
  let host = '';
  if (meetingUrl) {
    let url: URL;
    try { url = new URL(meetingUrl); } catch { throw new MeetingHostRefused('the meeting URL does not parse'); }
    if (url.protocol !== 'https:') throw new MeetingHostRefused('the meeting URL is not https');
    host = hostOf(url);
  }
  const onPlatform = !host || hostAllowed(host, platformDomains);
  let allow: OperatorAllowance | null = null;
  if (allowEntries?.length) {
    try { allow = parseAllowance(allowEntries, 'meetingHostAllow'); } catch (e) {
      throw new MeetingHostRefused(String((e as Error).message));
    }
  }
  return {
    platform,
    domains: onPlatform ? [...platformDomains] : [...platformDomains, host],
    customHost: onPlatform ? null : host,
    allow,
  };
}

function hostOf(url: URL): string {
  return url.hostname.replace(/^\[|\]$/g, '').replace(/\.+$/, '').toLowerCase();
}

/** The checks that need no lookup: an internal literal or a deployment-only name is refused for any
 *  request; for a top-level navigation the host must also be on the policy's list. */
export function checkWithoutLookup(url: URL, policy: NavigationPolicy, topLevel: boolean): Verdict {
  if (HOSTLESS_SCHEMES.has(url.protocol) || (url.protocol !== 'http:' && url.protocol !== 'https:')) return OK;
  const host = hostOf(url);
  const listed = allowanceAdmitsHost(policy.allow, host);
  if (literalAddress(host)) {
    if (allowanceAdmitsIp(policy.allow, host)) return OK;
    if (isBlockedIp(host)) return { ok: false, host, why: 'an internal address' };
  } else if (isBlockedHostname(host) && !listed) {
    return { ok: false, host, why: 'a name that only reaches this deployment' };
  }
  if (topLevel && !listed && !hostAllowed(host, policy.domains) && !allowanceAdmitsIp(policy.allow, host)) {
    return { ok: false, host, why: `not a ${policy.platform} host` };
  }
  return OK;
}

/** A navigation's full check: the checks above, then every address the host resolves to must be
 *  public or admitted by the operator's allowance. A name that does not resolve is left to the
 *  browser (it fails to load on its own). */
export async function checkNavigation(url: URL, policy: NavigationPolicy, topLevel: boolean,
  resolver: Resolver = resolveHost): Promise<Verdict> {
  const first = checkWithoutLookup(url, policy, topLevel);
  if (!first.ok || (url.protocol !== 'http:' && url.protocol !== 'https:')) return first;
  const host = hostOf(url);
  if (literalAddress(host)) return OK;
  const ips = await resolver(host);
  const inside = ips.find((ip) => isBlockedIp(ip) && !allowanceAdmitsIp(policy.allow, ip, host));
  return inside ? { ok: false, host, why: `resolves to an internal address (${inside})` } : OK;
}

/** The Chromium flag that pins the meeting's own host (when it is not a platform domain) to the
 *  address checked here — or MeetingHostRefused when that host resolves inside. `[]` for a meeting
 *  on a platform domain, or a host that does not resolve (the browser then fails to load it). */
export async function pinMeetingHostArgs(policy: NavigationPolicy, resolver: Resolver = resolveHost): Promise<string[]> {
  const host = policy.customHost;
  if (!host || literalAddress(host)) return [];
  const verdict = await checkNavigation(new URL(`https://${host}/`), policy, true, resolver);
  if (!verdict.ok) throw new MeetingHostRefused(`the meeting host ${verdict.host} is ${verdict.why}`);
  const ips = await resolver(host);
  if (!ips.length) return [];
  const ip = ips.find((a) => !a.includes(':')) ?? ips[0];
  return [`--host-resolver-rules=MAP ${host} ${ip.includes(':') ? `[${ip}]` : ip}`];
}

type Route = Parameters<Parameters<BrowserContext['route']>[1]>[0];
type Request = ReturnType<Route['request']>;
type CDPSession = Awaited<ReturnType<BrowserContext['newCDPSession']>>;

export interface NavigationGuard {
  /** The first refused top-level navigation, as a join failure reason; null while none. */
  refusal(): string | null;
}

export interface GuardOptions {
  resolver?: Resolver;
  log?: (line: string) => void;
}

function isTopLevel(request: Request): boolean {
  try { return request.frame().parentFrame() === null; } catch { return false; }
}

/** Hold every navigation of `context` (every page, popup and frame in it) to `policy`. */
export async function guardMeetingNavigation(context: BrowserContext, policy: NavigationPolicy,
  opts: GuardOptions = {}): Promise<NavigationGuard> {
  const resolver = opts.resolver ?? resolveHost;
  const log = opts.log ?? ((line: string) => console.warn(line));
  let refused: string | null = null;
  const refuse = (verdict: Exclude<Verdict, { ok: true }>, topLevel: boolean, how: string) => {
    const line = `navigation failed — ${how} to ${verdict.host} refused: ${verdict.why}`;
    log(`[bot] ${MEETING_HOST_REFUSED}: ${line}`);
    if (topLevel && !refused) refused = `${MEETING_HOST_REFUSED}: ${line}`;
  };

  // 1. The first request of every navigation, and every subresource.
  await context.route(() => true, async (route: Route) => {
    const request = route.request();
    let url: URL;
    try { url = new URL(request.url()); } catch { return route.fallback(); }
    const navigation = request.isNavigationRequest();
    const topLevel = navigation && isTopLevel(request);
    const verdict = navigation
      ? await checkNavigation(url, policy, topLevel, resolver)
      : checkWithoutLookup(url, policy, false);
    if (verdict.ok) {
      if (topLevel) log(`[bot] navigation guard: allowed a navigation to ${url.hostname}`);
      return route.fallback();
    }
    refuse(verdict, topLevel, navigation ? 'a navigation' : 'a request');
    return route.abort('blockedbyclient');
  });

  // 2. Every redirect hop of a document, before the browser follows it.
  const watchRedirects = async (page: Page) => {
    let cdp: CDPSession;
    try {
      cdp = await context.newCDPSession(page);
      const tree = await cdp.send('Page.getFrameTree') as { frameTree: { frame: { id: string } } };
      const mainFrameId = tree.frameTree.frame.id;
      cdp.on('Fetch.requestPaused', (ev: {
        requestId: string; frameId?: string; request: { url: string };
        responseStatusCode?: number; responseHeaders?: Array<{ name: string; value: string }>;
      }) => {
        void (async () => {
          const status = ev.responseStatusCode ?? 0;
          const location = (ev.responseHeaders ?? []).find((h) => h.name.toLowerCase() === 'location')?.value;
          if (status >= 300 && status < 400 && location) {
            let target: URL | null = null;
            try { target = new URL(location, ev.request.url); } catch { target = null; }
            const topLevel = ev.frameId === mainFrameId;
            const verdict = target ? await checkNavigation(target, policy, topLevel, resolver) : OK;
            if (!verdict.ok) {
              refuse(verdict, topLevel, 'a redirect');
              await cdp.send('Fetch.failRequest', { requestId: ev.requestId, errorReason: 'BlockedByClient' });
              return;
            }
            if (topLevel && target) log(`[bot] navigation guard: allowed a redirect to ${target.hostname}`);
          }
          await cdp.send('Fetch.continueRequest', { requestId: ev.requestId });
        })().catch(() => { /* the page or request may be gone */ });
      });
      await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*', resourceType: 'Document', requestStage: 'Response' }] });
    } catch (e) {
      log(`[bot] navigation guard: redirect hops are checked where they land only (${String(e)})`);
    }
  };

  // Where a redirect landed anyway (no CDP session): a top-level frame off the list is blanked and
  // recorded. Its request has left by then; layer 2 is what stops it before it does.
  const watchLandings = (page: Page) => page.on('framenavigated', (frame) => {
    if (frame.parentFrame() !== null) return;
    let url: URL;
    try { url = new URL(frame.url()); } catch { return; }
    const verdict = checkWithoutLookup(url, policy, true);
    if (verdict.ok) return;
    refuse(verdict, true, 'a landing');
    frame.goto('about:blank').catch(() => { /* the frame may be gone */ });
  });

  for (const page of context.pages()) { await watchRedirects(page); watchLandings(page); }
  context.on('page', (page) => { void watchRedirects(page); watchLandings(page); });

  return { refusal: () => refused };
}

/** The join driver with the guard's verdict on top: when a top-level navigation was refused, the
 *  join fails with the `meeting_host_refused` reason, whatever the platform layer saw afterwards
 *  (a blank page, a navigation error). The `navigation failed` words classify it as a navigation
 *  failure in join-evidence. */
export function withNavigationRefusal(driver: JoinDriver, guard: NavigationGuard): JoinDriver {
  const refused = (): JoinResult | null => {
    const reason = guard.refusal();
    return reason ? { outcome: 'error', reason, signals: driver.lastSignals?.() } : null;
  };
  return {
    ...driver,
    async join(report) {
      let result: JoinOutcome | JoinResult;
      try {
        result = await driver.join(report);
      } catch (e) {
        const r = refused();
        if (r) return r;
        throw e;
      }
      const outcome = typeof result === 'string' ? result : result.outcome;
      return outcome === 'admitted' ? result : (refused() ?? result);
    },
  };
}

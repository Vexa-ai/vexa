/**
 * navigation — where an AUTHENTICATED browser may go.
 *
 * An authenticated bot runs on the deployment's stored session: the platform account's cookies,
 * restored into this bot's profile. Whatever a page can make that browser load as a document runs
 * with that session behind it. So an authenticated browser navigates (top level, popup or frame)
 * only to the meeting's own host and the platform's own domains; any other navigation is refused
 * and logged. Subresources (scripts, images, media, XHR) are not restricted: the meeting pages
 * load them from CDNs this list would have to chase, and a subresource is not a page the session
 * can be driven through.
 *
 * Two layers, because request interception sees only the first request of a navigation: a
 * navigation request to an off-list host is aborted before it leaves; a navigation that a redirect
 * carries off the list is caught where it lands, and that frame is sent to about:blank.
 */
import type { BrowserContext, Frame, Page, Route } from 'playwright';
import type { AuthPlatform } from './types';

/** The domains an authenticated session's navigations may reach, per platform (a domain also
 *  covers its subdomains). Sign-in and account flows included; nothing else. */
export const AUTH_NAVIGATION_DOMAINS: Record<AuthPlatform, readonly string[]> = {
  google: ['google.com', 'youtube.com', 'gstatic.com', 'googleusercontent.com', 'googleapis.com'],
  teams: ['microsoft.com', 'cloud.microsoft', 'microsoftonline.com', 'microsoftonline-p.com', 'live.com',
    'office.com', 'office.net', 'skype.com', 'msauth.net', 'msftauth.net'],
  zoom: ['zoom.us', 'zoom.com', 'zoomgov.com'],
};

/** Schemes a navigation may use without naming a host (blank frames, blobs, Chromium's own error
 *  pages). */
const HOSTLESS_SCHEMES = new Set(['about:', 'data:', 'blob:', 'chrome-error:']);

/** Whether `host` is one of `domains` or a subdomain of one. */
export function hostAllowed(host: string, domains: readonly string[]): boolean {
  const h = host.toLowerCase().replace(/\.$/, '');
  return domains.some((d) => h === d || h.endsWith(`.${d}`));
}

export class MeetingHostRefused extends Error {
  constructor(message: string) { super(message); this.name = 'MeetingHostRefused'; }
}

/**
 * The domains an authenticated browser may navigate to: the platform's own, and nothing the caller
 * names. The meeting URL is checked against them — its host must be one of the platform's domains
 * or a subdomain of one (a Zoom tenant's `<org>.zoom.us`, Teams on `teams.microsoft.com` /
 * `teams.live.com`) — and an authenticated session for a platform with no stored-session domains
 * (Jitsi) is refused. Throws {@link MeetingHostRefused}; the host never widens the list.
 */
export function authenticatedNavigationDomains(platform: AuthPlatform | null, meetingUrl: string | null): string[] {
  if (!platform) throw new MeetingHostRefused('an authenticated session is only for Google Meet, Teams or Zoom meetings');
  const domains = [...AUTH_NAVIGATION_DOMAINS[platform]];
  let url: URL;
  try { url = new URL(meetingUrl || ''); } catch {
    throw new MeetingHostRefused('the meeting URL does not parse');
  }
  if (url.protocol !== 'https:' || !hostAllowed(url.hostname, domains)) {
    throw new MeetingHostRefused(`the meeting host is not a ${platform} host: ${url.hostname}`);
  }
  return domains;
}

/**
 * The flags of an authenticated launch with Chromium's site isolation ON: every site in a renderer
 * process of its own (`--site-per-process`), and the flags that turned it off removed — including
 * from a `--disable-features` list, whose other features are kept.
 */
export function withSiteIsolation(args: readonly string[]): string[] {
  const off = new Set(['IsolateOrigins', 'site-per-process']);
  const out: string[] = [];
  for (const arg of args) {
    if (arg === '--disable-site-isolation-trials' || arg === '--site-per-process') continue;
    if (arg.startsWith('--disable-features=')) {
      const kept = arg.slice('--disable-features='.length).split(',').filter((f) => f && !off.has(f));
      if (kept.length) out.push(`--disable-features=${kept.join(',')}`);
      continue;
    }
    out.push(arg);
  }
  out.push('--site-per-process');
  return out;
}

/** Whether a URL is off the list (and so must not be navigated to). */
export function offList(url: URL, domains: readonly string[]): boolean {
  if (HOSTLESS_SCHEMES.has(url.protocol)) return false;
  return !hostAllowed(url.hostname, domains);
}

/**
 * Refuse every navigation of `context` (every page, popup and frame in it) to a host off `domains`.
 * Requests to such hosts that are not navigations go through untouched.
 */
export async function restrictNavigation(
  context: BrowserContext,
  domains: readonly string[],
  log: (line: string) => void = (line) => console.warn(line),
): Promise<void> {
  const hostOf = (url: string): string => { try { return new URL(url).hostname; } catch { return ''; } };
  await context.route((url) => offList(url, domains), async (route: Route) => {
    const request = route.request();
    if (!request.isNavigationRequest()) return route.continue();
    log(`[remote-browser] refused a navigation off the authenticated session's hosts: ${hostOf(request.url())}`);
    return route.abort('blockedbyclient');
  });
  const guard = (page: Page) => page.on('framenavigated', (frame: Frame) => {
    let url: URL;
    try { url = new URL(frame.url()); } catch { return; }
    if (!offList(url, domains)) return;
    log(`[remote-browser] a redirect carried a frame off the authenticated session's hosts: ${url.hostname}; blanked`);
    frame.goto('about:blank').catch(() => { /* the frame may be gone */ });
  });
  context.pages().forEach(guard);
  context.on('page', guard);
}

/**
 * host-match.ts — the ONE rule this package uses to decide whether a URL belongs to a platform.
 *
 * A hostname's registrable domain is its RIGHTMOST part, so the only sound test for "is this
 * host Google Meet / Teams / Zoom / Jitsi" is equality with the platform's domain, or a dotted
 * suffix of it. Two weaker tests look right and are not:
 *
 *   • `url.includes("meet.google.com")` — matches anywhere in the URL, so the platform name can
 *     sit in a query string or a path segment of a host that is not the platform at all.
 *   • `host.endsWith("teams.live.com")` — no leading dot, so it also matches `notteams.live.com`.
 *
 * Both put the host under the control of whoever supplied the link. `hostMatches` is the
 * label-boundary version: the leading dot is what makes the suffix test a boundary rather than
 * a substring. `msteams/auth-redirect.ts` already carried this rule for the Microsoft sign-in
 * and Teams host lists; it now lives here so `resolvePlatform` and the redirect guard share one
 * implementation instead of drifting copies.
 */

/** The lowercased hostname of `url`, or null when it does not parse as an absolute URL. */
export function hostOf(url: string): string | null {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return null;
  }
}

/** True iff `host` IS one of `domains` or a dotted subdomain of one — never a substring match. */
export function hostMatches(host: string, ...domains: readonly string[]): boolean {
  return domains.some((d) => host === d || host.endsWith(`.${d}`));
}

/** `hostMatches` applied to a URL string; false when the URL does not parse. */
export function urlHostMatches(url: string, ...domains: readonly string[]): boolean {
  const host = hostOf(url);
  return host !== null && hostMatches(host, ...domains);
}

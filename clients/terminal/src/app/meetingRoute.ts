/** meetingRoute — the URL shape that makes an open meeting REFERENCEABLE.
 *
 *  Before this, the terminal was a single route (`/`): which meeting was open lived only in the
 *  dockview layout in localStorage. That survives a reload in the same browser, but the URL is not a
 *  reference — you cannot paste a meeting to a colleague, bookmark it, or reopen it in a fresh session.
 *
 *  The durable reference is `/meetings/<meeting id>`, where the id is the meetings-domain ROW id the
 *  client already keys everything by (`MeetingMock.id` — the same id `GET /api/transcripts/by-id/{id}`
 *  and the row-keyed live streams use). No API change is implied: the route only carries the id the
 *  existing client data paths already resolve. In-app navigation (clicking a row, the preview slot) is
 *  unchanged — the URL is kept in sync behind it.
 *
 *  Pure + dependency-free so the parsing/formatting contract is unit-tested without a DOM or a router. */

/** The single route prefix. */
export const MEETING_ROUTE_PREFIX = "/meetings/";

/** Ids we are willing to put in, and take out of, a path segment. Row ids are numeric today, but a
 *  reference may also carry a native meeting code (`abc-defg-hij`, `19:meeting_…@thread.v2`,
 *  `room@jitsi.example.org`), so the charset is permissive — and bounded, with no path separators,
 *  whitespace or wildcards, so a hostile URL can never widen into another route or a request path. */
const ID_RE = /^[A-Za-z0-9._:@=+-]{1,128}$/;

/** True if `id` is a syntactically usable meeting reference. Says nothing about existence — an id that
 *  passes here and resolves to nothing renders the not-found state. */
export function isMeetingRouteId(id: string): boolean {
  return ID_RE.test(id);
}

/** The canonical path for a meeting id, or `/` when the id is unusable. */
export function meetingPath(id: string): string {
  const v = (id ?? "").trim();
  return isMeetingRouteId(v) ? `${MEETING_ROUTE_PREFIX}${encodeURIComponent(v)}` : "/";
}

/** The meeting id carried by a pathname, or null when the path is not a meeting route.
 *  Tolerates a trailing slash and percent-encoding; rejects extra segments and bad ids. */
export function meetingIdFromPath(pathname: string | null | undefined): string | null {
  if (!pathname || !pathname.startsWith(MEETING_ROUTE_PREFIX)) return null;
  const rest = pathname.slice(MEETING_ROUTE_PREFIX.length).replace(/\/+$/, "");
  if (!rest || rest.includes("/")) return null;
  let id: string;
  try { id = decodeURIComponent(rest); } catch { return null; }   // malformed %-escape
  return isMeetingRouteId(id) ? id : null;
}

/** True if this pathname is one WE own (`/` or a meeting route) — the URL-sync effect only ever
 *  rewrites these two shapes, so it can never clobber a route someone else adds later. */
export function isOwnedPath(pathname: string | null | undefined): boolean {
  return pathname === "/" || meetingIdFromPath(pathname) !== null;
}

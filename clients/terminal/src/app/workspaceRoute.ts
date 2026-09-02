/** workspaceRoute — `/w/<workspace-id>/<path>`: the ONE canonical URL for a file.
 *
 *  PRD decision 26.2. `/meetings/<id>` made a meeting referenceable; this does the same for a
 *  document, and for the same reason — a URL is the only reference that works in a mail, in a chat
 *  and in somebody else's workspace at once. The id is the workspace's, not its slug, so the link
 *  keeps working after a rename: that is the whole decision, expressed as a route.
 *
 *  Two rules the shape enforces:
 *
 *    ACCESS IS NOT IN THE URL. A canonical link is handed to people who may not be able to open it,
 *    which is normal — *"if a workspace is not available, it's okay — by design"*. The route resolves
 *    the id against the server, which answers `readable` / `not-yours` / `gone` for THIS reader; the
 *    URL itself grants nothing.
 *
 *    ONE SHELL. `/w/…` renders the same terminal `/` does, exactly as `/meetings/<id>` does. Two
 *    shells is two things to keep in step, and the second one drifts.
 *
 *  Pure + dependency-free, so the parse/format contract is unit-tested with no DOM and no router.
 */

export const WORKSPACE_ROUTE_PREFIX = "/w/";

/** A workspace id: 10 chars of lowercase base32 (see `shared/workspace_id.py`). */
const ID_RE = /^[a-z2-7]{10}$/;

/** Path segments we are willing to take out of a URL. No `..`, no backslash, no control chars: a
 *  hostile link must not widen into another route or walk out of the workspace it names. The server
 *  refuses a traversal too — this is the near end of the same rule, because a URL is untrusted input
 *  wherever it is read. */
const SEGMENT_RE = /^[A-Za-z0-9._@+ -]{1,128}$/;
/** `.` and `..` pass SEGMENT_RE (dots are legal in a filename) and must never pass as a SEGMENT: a
 *  link is text somebody wrote into a document, so a URL built from one is untrusted input at both
 *  ends. The server refuses a traversal too; this is the near end of the same rule. */
const isSafeSegment = (s: string): boolean => SEGMENT_RE.test(s) && s !== "." && s !== "..";

export interface WorkspaceRoute { workspace: string; path: string }

export function isWorkspaceRouteId(id: string): boolean {
  return ID_RE.test(id ?? "");
}

/** The canonical path for a workspace id + workspace-relative path, or `/` when unusable. */
export function workspacePath(workspace: string, path = ""): string {
  if (!isWorkspaceRouteId(workspace)) return "/";
  const segs = (path ?? "").split("/").filter(Boolean);
  if (segs.some((s) => !isSafeSegment(s))) return `${WORKSPACE_ROUTE_PREFIX}${workspace}`;
  const tail = segs.map(encodeURIComponent).join("/");
  return tail ? `${WORKSPACE_ROUTE_PREFIX}${workspace}/${tail}` : `${WORKSPACE_ROUTE_PREFIX}${workspace}`;
}

/** `{workspace, path}` carried by a pathname, or null when it is not a workspace route.
 *  Tolerates a trailing slash and percent-encoding; refuses a bad id or an unsafe segment. */
export function workspaceRouteFromPath(pathname: string | null | undefined): WorkspaceRoute | null {
  if (!pathname || !pathname.startsWith(WORKSPACE_ROUTE_PREFIX)) return null;
  const rest = pathname.slice(WORKSPACE_ROUTE_PREFIX.length).replace(/\/+$/, "");
  if (!rest) return null;
  const [id, ...tail] = rest.split("/");
  if (!isWorkspaceRouteId(id)) return null;
  let segs: string[];
  try { segs = tail.map(decodeURIComponent); } catch { return null; }   // malformed %-escape
  if (segs.some((s) => !isSafeSegment(s))) return null;
  return { workspace: id, path: segs.join("/") };
}

/** True if this pathname is one WE own — so a URL-sync effect can never clobber a route
 *  somebody else adds later. Mirrors `meetingRoute.isOwnedPath`. */
export function isWorkspacePath(pathname: string | null | undefined): boolean {
  return workspaceRouteFromPath(pathname) !== null;
}

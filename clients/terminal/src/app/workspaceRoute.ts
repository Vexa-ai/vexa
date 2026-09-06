/** workspaceRoute — `/w/<workspace>/<path>`: the ONE canonical URL for a file.
 *
 *  PRD decision 26.2. `/meetings/<id>` made a meeting referenceable; this does the same for a
 *  document, and for the same reason — a URL is the only reference that works in a mail, in a chat
 *  and in somebody else's workspace at once. The CANONICAL form names the workspace's id, not its
 *  slug, so the link keeps working after a rename: that is the whole decision, expressed as a route.
 *
 *  ⚠ …AND IT ACCEPTS A SLUG TOO (Vexa-ai/vexa#1643). The admin opened
 *  `/w/oenb-b5e60c/README.md` — a shared workspace of his own, addressed the way every other
 *  surface in the product still spells a workspace — and the route did not recognise it as a route
 *  at all: `oenb-b5e60c` is not ten characters of base32, so the parse returned null, the page
 *  dispatched nothing, and the terminal opened on whatever it opens on. A URL that silently is not
 *  a URL is the worst of the three answers available; the other two (open it, or say why not) are
 *  both better, and which one applies is the SERVER's to say.
 *
 *  So the ref is either shape and neither is authorized here:
 *
 *    ACCESS IS NOT IN THE URL. A canonical link is handed to people who may not be able to open it,
 *    which is normal — *"if a workspace is not available, it's okay — by design"*. The route resolves
 *    the ref against the server, which answers `readable` / `not-yours` / `gone` for THIS reader; the
 *    URL itself grants nothing. Widening what the route PARSES widens nothing about what it OPENS.
 *
 *    ONE SHELL. `/w/…` renders the same terminal `/` does, exactly as `/meetings/<id>` does. Two
 *    shells is two things to keep in step, and the second one drifts.
 *
 *  Pure + dependency-free, so the parse/format contract is unit-tested with no DOM and no router.
 */

export const WORKSPACE_ROUTE_PREFIX = "/w/";

/** A workspace id: 10 chars of lowercase base32 (see `shared/workspace_id.py`). */
const ID_RE = /^[a-z2-7]{10}$/;

/** A workspace SLUG — the directory a workspace lives in today (`126`, `oenb-b5e60c`, `_global`).
 *  Deliberately narrow: letters, digits, `_`, `.` and `-`, never leading with a dot, so no dot-
 *  namespaced tree (`.system`, `.attached`) and no `.`/`..` can be spelled as one. It is a NAME
 *  test and not an authorization: a slug that passes here and means nothing to the registry comes
 *  back `gone`, and one that belongs to somebody else comes back `not-yours`. */
const SLUG_RE = /^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$/;

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

/** Anything the route is willing to READ as a workspace — its id, or the slug it lives under.
 *  Which of the two it is decides which resolver the shell asks (`minutes/deepLink.ts`); it never
 *  decides whether the page opens. */
export function isWorkspaceRouteRef(ref: string): boolean {
  const v = ref ?? "";
  return isWorkspaceRouteId(v) || SLUG_RE.test(v);
}

/** The canonical path for a workspace ref + workspace-relative path, or `/` when unusable. */
export function workspacePath(workspace: string, path = ""): string {
  if (!isWorkspaceRouteRef(workspace)) return "/";
  const segs = (path ?? "").split("/").filter(Boolean);
  if (segs.some((s) => !isSafeSegment(s))) return `${WORKSPACE_ROUTE_PREFIX}${workspace}`;
  const tail = segs.map(encodeURIComponent).join("/");
  return tail ? `${WORKSPACE_ROUTE_PREFIX}${workspace}/${tail}` : `${WORKSPACE_ROUTE_PREFIX}${workspace}`;
}

/** `{workspace, path}` carried by a pathname, or null when it is not a workspace route.
 *  Tolerates a trailing slash and percent-encoding; refuses a bad ref or an unsafe segment. */
export function workspaceRouteFromPath(pathname: string | null | undefined): WorkspaceRoute | null {
  if (!pathname || !pathname.startsWith(WORKSPACE_ROUTE_PREFIX)) return null;
  const rest = pathname.slice(WORKSPACE_ROUTE_PREFIX.length).replace(/\/+$/, "");
  if (!rest) return null;
  const [raw, ...tail] = rest.split("/");
  let id: string;
  try { id = decodeURIComponent(raw); } catch { return null; }   // malformed %-escape
  if (!isWorkspaceRouteRef(id)) return null;
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

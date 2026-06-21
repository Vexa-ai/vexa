/**
 * P20 / ADR-0012 — Complete mediation: authorize every read of a user-owned
 * resource through ONE seam, default-deny in spirit (owner-only here).
 *
 * This is the SEAM, not the policy. The desktop's read paths — GET /transcripts,
 * GET /recordings (+ /player), GET /bots, and the /ws subscribe — all route a
 * `canAccess(subject, resource, action)` decision through this port BEFORE
 * serving. A path with no `canAccess` call is the bug ADR-0012 guards against.
 *
 * `resource` is the meeting key (`platform/native_meeting_id`, i.e. desktop's
 * `keyOf`). `subject` is derived from the request (X-API-Key header value, else
 * 'local'; the /ws path has no headers, so the `api_key` query param, else
 * 'local'). `action` is 'read' on every read path; 'write' is reserved for when
 * write paths route through the same port.
 *
 * The DEFAULT adapter (`ownerOnly`) allows everything: the all-in-one localhost
 * is a single local user who owns every meeting, so owner-only ≡ allow-all here.
 * That is the documented P16 default — the seam ships now; real grants
 * (owner_id / visibility / an access_grants table, ADR-0003) land ADDITIVELY
 * behind this same port when sharing ships, with no read path needing to change.
 * The cloud meeting-api inherits the seam rather than reinventing it.
 */
export type CanAccess = (subject: string, resource: string, action: 'read' | 'write') => boolean;

/** P16 default: the single local user owns every meeting → allow. */
export const ownerOnly: CanAccess = () => true;

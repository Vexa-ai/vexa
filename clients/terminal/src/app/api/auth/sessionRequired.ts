/** What the terminal knows about a browser's credential and the agent's "a session is required"
 *  refusal (D13). A browser whose cookie holds a KEY rather than a session — one that signed in before
 *  this deployment minted sessions — is never traded up in place: it signs in again, and that sign-in
 *  mints its session. Not a route file. */
/** Is this upstream refusal "a session is required" (agent-api's D13 refusal)? */
export function isSessionRequired(status: number, body: string): boolean {
  if (status !== 403) return false;
  try {
    const d = (JSON.parse(body) as { detail?: { refused?: unknown } })?.detail;
    return !!d && typeof d === "object" && d.refused === "session_required";
  } catch {
    return false;
  }
}

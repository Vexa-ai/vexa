/** Session liveness — the ONE signal that says "the credential this tab is holding is no longer
 *  being accepted", and the ONE listener seam the login gate uses to react to it.
 *
 *  WHY THIS EXISTS (2026-09-01). A terminal session can die UNDER a rendered app: a login token is
 *  revoked server-side and nothing in the browser is told. The login gate probes `/api/auth/me`
 *  once, on mount, and never again — so the shell kept rendering in full while every request behind
 *  it 401'd, and the user's only clue was a chat turn ending in a generic "something went wrong".
 *  A dead session must LOOK like a dead session.
 *
 *  Shape: a window CustomEvent rather than a shared store, because the two ends live in different
 *  layers on purpose — the raisers are the HTTP chokepoints (`apiClient.getJson`, `chatStream`),
 *  the lowerer is `AuthGate`, and neither should import the other. One named raiser, one named
 *  listener, one value — the desk rule about two writers on one surface applies here too.
 *
 *  THE EVENT IS A SUSPICION, NOT A VERDICT. A 403 is very often resource-scoped ("you are not in
 *  that workspace") and says nothing about the session; even a 401 can come from one misbehaving
 *  upstream. So this module never decides anything — it reports that an auth-shaped refusal
 *  happened, and the listener re-probes `/api/auth/me` (which validates the token against the same
 *  oracle the real routes use) to find out whether the SESSION is actually gone. Signing somebody
 *  out on an unverified guess would be its own defect. */

/** The window event name. Exported so tests can dispatch it without importing the raiser. */
export const SESSION_EXPIRED_EVENT = "vexa:session-suspect";

/** What the raiser hands the listener: the refusing status and the URL that refused, for logging. */
export interface SessionSuspectDetail {
  status: number;
  url?: string;
}

/** Auth-shaped refusals — the only statuses that can mean "your credential is not accepted". */
export function isAuthStatus(status: number): boolean {
  return status === 401 || status === 403;
}

/** Raise the suspicion. A no-op off `status` that isn't auth-shaped, and off the server (no window),
 *  so callers can invoke it unconditionally at their failure site. */
export function noteAuthFailure(status: number, url?: string): void {
  if (!isAuthStatus(status)) return;
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  window.dispatchEvent(
    new CustomEvent<SessionSuspectDetail>(SESSION_EXPIRED_EVENT, { detail: { status, url } }),
  );
}

/** Subscribe. Returns the unsubscribe so a React effect can just `return onSessionSuspect(...)`. */
export function onSessionSuspect(fn: (detail: SessionSuspectDetail) => void): () => void {
  if (typeof window === "undefined" || typeof window.addEventListener !== "function") return () => {};
  const handler = (e: Event) => fn((e as CustomEvent<SessionSuspectDetail>).detail ?? { status: 401 });
  window.addEventListener(SESSION_EXPIRED_EVENT, handler);
  return () => window.removeEventListener(SESSION_EXPIRED_EVENT, handler);
}

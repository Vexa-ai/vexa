/** Platforms we can hold a logged-in session for. */
export type AuthPlatform = 'zoom' | 'google' | 'teams';

export interface LoginStatus {
  loggedIn: boolean;
  /** Best-effort signed-in account identifier, when we can read it. */
  user?: string;
  /** Human-readable evidence (url / cookie / signed-out markers). */
  detail?: string;
}

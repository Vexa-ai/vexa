/** Per-user onboarding flag — persisted in localStorage so onboarding fires EXACTLY ONCE per user and
 *  its state survives reloads (never re-triggered by a page refresh). Keyed by the user's identity so
 *  switching users is clean. */

const KEY = (uid: string) => `vexa.terminal.onboarded.${uid}`;

/** Has this user already been onboarded? */
export function isOnboarded(uid: string): boolean {
  try { return localStorage.getItem(KEY(uid)) === "1"; } catch { return false; }
}

/** Mark this user onboarded (the durable bool — set once the onboarding kickoff has run). */
export function setOnboarded(uid: string, done: boolean): void {
  try {
    if (done) localStorage.setItem(KEY(uid), "1");
    else localStorage.removeItem(KEY(uid));
  } catch { /* storage unavailable — onboarding just re-fires, harmless */ }
}

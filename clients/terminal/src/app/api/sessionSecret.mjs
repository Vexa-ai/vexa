/** The terminal's session secret at boot (`VEXA_TERMINAL_SESSION_SECRET`).
 *
 *  Plain JavaScript on purpose: `server.mjs` imports it to refuse to start before Next is loaded.
 *
 *  The secret is what lets the terminal mint a SESSION credential at admin-api's
 *  `POST /internal/sessions` (auth/adminApi.ts `mintSessionToken`). Without one, every sign-in falls
 *  back to an ordinary key and every decision that needs a session is refused, while the terminal
 *  looks healthy. Every deploy surface generates it at install (compose `mint-dev-env.sh`, the
 *  chart's Secret, Lite's entrypoint), so a production terminal without a usable one is a broken
 *  deployment and refuses to start, naming the key. `next dev` (NODE_ENV other than `production`)
 *  keeps the fallback, for a checkout running the terminal alone.
 */
import { secretProblem } from "./auth/authSecret.mjs";

export const SESSION_SECRET_KEY = "VEXA_TERMINAL_SESSION_SECRET";

/** The reason the terminal must not start with this environment, or null when it may.
 *  @param {Record<string, string | undefined>} env
 *  @returns {string | null} */
export function sessionSecretStartupError(env) {
  if (env.NODE_ENV !== "production") return null;
  const problem = secretProblem(env[SESSION_SECRET_KEY], SESSION_SECRET_KEY);
  if (!problem) return null;
  return `${problem}; set it to the value admin-api holds (every deploy surface generates one at `
    + "install, e.g. `openssl rand -hex 32`) — without it no sign-in can end in a session";
}

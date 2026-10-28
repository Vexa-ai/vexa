/** version.ts — what THIS bundle is, and which agent-api contract it was built against.
 *
 *  Both constants exist for PRD decision 39: swaps on the dogfood stack are invisible now, so the
 *  two jobs the "out / in" ritual used to do by hand have to be done by machinery.
 *
 *  `TERMINAL_AGENT_API` is the pairing number (F55/F77). The failure it prevents was live on
 *  2026-09-02 twice: a terminal built ahead of the server shipped a button whose request every
 *  running agent-api rejected, and a terminal swapped onto an old server lost the note tab. The
 *  deploy script reads this number off the IMAGE (the `ai.vexa.terminal.agent_api` label, which the
 *  Dockerfile copies from here and a test pins) and refuses to put a terminal in front of a person
 *  when the agent-api that is actually serving answers a different `api` on `GET /api/version`.
 *  Bump it only together with agent-api's `API_VERSION` — i.e. on a client-visible break.
 *
 *  `terminalBuild()` is the build stamp, read at call time so the server route and the tests see
 *  the env rather than a value frozen at module load. It is inlined into the client bundle at BUILD
 *  time like every other NEXT_PUBLIC_*, which is exactly the property the reload bar needs: when a
 *  new bundle is served under an open tab, the tab's own copy of this string stops matching the one
 *  the server route reports, and that difference IS the notification.
 */

/** The agent-api contract this bundle speaks. Keep in lockstep with `API_VERSION` in
 *  `core/agent/control_plane/version.py` and with the Dockerfile's `ai.vexa.terminal.agent_api`. */
export const TERMINAL_AGENT_API = 1;

export const UNKNOWN_BUILD = "unknown";

/** The build this bundle came out of — `NEXT_PUBLIC_BUILD_ID`, stamped at image build. */
export function terminalBuild(): string {
  return (process.env.NEXT_PUBLIC_BUILD_ID || "").trim() || UNKNOWN_BUILD;
}

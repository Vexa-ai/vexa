/** Server-side gate for meetings-only mode (NEXT_PUBLIC_TERMINAL_MODE=meetings — see src/app/mode.ts).
 *
 *  The catch-all proxy routes by path: `meetings|transcripts|bots` → the gateway ROOT (meeting-api);
 *  everything else → the gateway's /agent/* prefix (agent-api). In meetings mode the agent branch must
 *  be REFUSED at the edge (404), not merely hidden in the UI — a hand-crafted request must not reach
 *  agent-api either. Kept as a pure predicate (path in, decision out) so it is provable in isolation
 *  (proxyMode.test.ts) without Next request plumbing.
 */
import { meetingsOnly } from "../mode";

/** The meeting-domain paths the catch-all forwards to the gateway ROOT (mirrors MEETINGS_DOMAIN there).
 *  `user` covers the identity-domain self-serve configs the gateway exposes at its root
 *  (/user/webhook, /user/calendar) — same authenticated edge, admin-api behind it. */
export const MEETINGS_DOMAIN = /^(meetings|transcripts|bots|user)(\/|$)/;

/** true when this /api/<path> must be refused (meetings mode + a non-meeting-domain path). */
export function refusedInMeetingsMode(path: string): boolean {
  return meetingsOnly() && !MEETINGS_DOMAIN.test(path);
}

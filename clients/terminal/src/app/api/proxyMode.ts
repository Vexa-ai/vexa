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

/** ALLOY: Telemetry joins the meeting domain only while its server opt-in is enabled. */
export const ALLOY_DOMAIN = /^alloy(\/|$)/;

/** ALLOY: Pure path decision shared by routing and the meetings-only edge gate. */
export function isMeetingsDomain(
  path: string,
  alloyEnabled: boolean,
): boolean {
  return MEETINGS_DOMAIN.test(path) ||
    (alloyEnabled && ALLOY_DOMAIN.test(path));
}

/** ALLOY: Refuse disabled telemetry at the meetings-only edge before any fetch. */
export function refusedInMeetingsMode(
  path: string,
  alloyEnabled: boolean,
): boolean {
  return meetingsOnly() && !isMeetingsDomain(path, alloyEnabled);
}

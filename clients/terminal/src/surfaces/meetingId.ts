/** Meeting-link → {platform, native_meeting_id} parsing + validation for the "Add bot" flow.
 *  Id formats mirror the dashboard join-form (clients/dashboard/src/components/join/join-form.tsx):
 *    google_meet → abc-defg-hij   ·   zoom → 9–11 digits   ·   teams → non-empty (passcode handled elsewhere)
 *    jitsi → the meet.jit.si room name, or room@host for a self-hosted deployment (a single
 *    URL-safe path segment; declared VEXA_JITSI_HOSTS arrive via the `jitsiHosts` parameter).
 *  Accepts either a raw id or a full meeting URL the user pasted.
 *  Hosts are matched EXACTLY or as a dotted subdomain (`hostMatches`) — never by substring,
 *  which would read `meet.google.com.attacker.example` as Google Meet. The server parsers
 *  (`meeting_api.collector.meeting_link`, `vexa_mcp.link_parser`) carry the same helper. */

export type Platform = "google_meet" | "teams" | "zoom" | "jitsi";

export interface ParsedMeeting {
  platform: Platform;
  native_meeting_id: string;
}

const GMEET_ID = /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/;
const ZOOM_ID = /\d{9,11}/;
// A Jitsi room: one URL-safe path segment (no separators/whitespace) — the id is embedded
// back into the construct-URL template, so the encoded form is the id.
const JITSI_ROOM = /^[^/?#\s]+$/;

// Zoom's meeting domains: canonical zoom.us (+ every regional subdomain — us02web, a
// customer's company.zoom.us) and the US-government tenant. Matches the server parsers.
const ZOOM_DOMAINS = ["zoom.us", "zoomgov.com"] as const;
// Every domain a hosted platform claims. Used both to match and to recognize a lookalike.
const PLATFORM_DOMAINS = ["meet.google.com", ...ZOOM_DOMAINS, "teams.microsoft.com", "teams.live.com"] as const;

/** Exact host, or a subdomain of one of `domains` — never a substring match.
 *  A substring test (`host.includes("meet.google.com")`) also accepts
 *  `meet.google.com.attacker.example`: the platform name is a *prefix* of a domain whoever
 *  pasted the link controls. The registrable domain is the rightmost part of a hostname, so
 *  the only sound test is equality or a dotted suffix. */
function hostMatches(host: string, ...domains: readonly string[]): boolean {
  return domains.some((d) => host === d || host.endsWith(`.${d}`));
}

/** True when `host` merely CONTAINS a platform's domain without being it or a subdomain of it.
 *  Such a host must not reach the jitsi naming heuristics either: `meet.google.com.attacker.example`
 *  carries a "meet" LABEL, so the self-hosted fallback would otherwise adopt it as a jitsi room.
 *  Declared hosts (`jitsiHosts`) are unaffected — an operator naming their deployment is not a guess. */
function isPlatformLookalike(host: string): boolean {
  return PLATFORM_DOMAINS.some((d) => host.includes(d) && !hostMatches(host, d));
}

/** True if `id` is a valid native id for `platform`. */
export function isValidMeetingId(platform: Platform, id: string): boolean {
  const v = id.trim();
  if (!v) return false;
  if (platform === "google_meet") return GMEET_ID.test(v.toLowerCase());
  if (platform === "zoom") return /^\d{9,11}$/.test(v);
  if (platform === "jitsi") return JITSI_ROOM.test(v);
  return v.length > 0; // teams
}

/** Parse a pasted Google Meet / Teams / Zoom / Jitsi link (or bare id) into a platform + native id.
 *  Returns null when nothing valid can be extracted. `jitsiHosts` is the deployment's
 *  VEXA_JITSI_HOSTS list (served by /api/meeting/jitsi-hosts) — declared hosts are recognized
 *  as jitsi even without jitsi/meet naming, matching the server parser. */
export function parseMeetingInput(raw: string, jitsiHosts: readonly string[] = []): ParsedMeeting | null {
  const input = raw.trim();
  if (!input) return null;

  // Bare Google Meet code, e.g. "abc-defg-hij"
  if (GMEET_ID.test(input.toLowerCase())) {
    return { platform: "google_meet", native_meeting_id: input.toLowerCase() };
  }

  let url: URL | null = null;
  try {
    url = new URL(input);
  } catch {
    url = null;
  }

  if (url) {
    const host = url.hostname.toLowerCase();
    if (hostMatches(host, "meet.google.com")) {
      const code = url.pathname.split("/").filter(Boolean).pop()?.toLowerCase() ?? "";
      return isValidMeetingId("google_meet", code) ? { platform: "google_meet", native_meeting_id: code } : null;
    }
    if (hostMatches(host, ...ZOOM_DOMAINS)) {
      const m = url.pathname.match(ZOOM_ID) || url.search.match(ZOOM_ID);
      return m ? { platform: "zoom", native_meeting_id: m[0] } : null;
    }
    if (hostMatches(host, "teams.microsoft.com", "teams.live.com")) {
      // Classic deep link carries the thread id (…/l/meetup-join/19:meeting_…@thread.v2).
      const decoded = decodeURIComponent(input);
      const thread = decoded.match(/19:meeting_[^@%\s/]+@thread\.v2/i);
      if (thread) return { platform: "teams", native_meeting_id: thread[0] };
      // New short meeting link: teams.microsoft.com/meet/<id>?p=<passcode> — the native id is the path
      // segment; the passcode rides along in `meeting_url` (sent verbatim by the Add-bot call).
      const short = url.pathname.match(/\/meet\/([^/?#]+)/i);
      if (short) return { platform: "teams", native_meeting_id: short[1] };
      return null;
    }
    // Jitsi: LAST, so every known platform above claims its hosts first (mirrors the server
    // parser's ordering). The canonical public deployment, the deployment-declared hosts
    // (VEXA_JITSI_HOSTS via `jitsiHosts` — same setting the server parser honours), plus the
    // common self-hosted conventions — a host containing "jitsi" (jitsi.example.org) or a
    // "meet" hostname LABEL anywhere (meet.example.org, eu.meet.example.org — jitsi's own
    // recommended naming, regionalized). The room is the path's single segment, kept exactly
    // as pasted (case + percent-encoding preserved — the raw URL rides along as meeting_url,
    // so the bot lands on the right deployment).
    // A host that merely LOOKS like a hosted platform is excluded from the naming heuristics
    // outright: the branches above already refused it by name, and
    // meet.google.com.attacker.example carries a "meet" label that would otherwise let it back
    // in through this door. Declared hosts are an explicit opt-in and stay unaffected.
    const jitsiHost =
      host === "meet.jit.si" || jitsiHosts.includes(host) ||
      (!isPlatformLookalike(host) && (host.includes("jitsi") || host.split(".").includes("meet")));
    if (jitsiHost) {
      const room = url.pathname.replace(/^\/+|\/+$/g, "");
      if (!room || !JITSI_ROOM.test(room)) return null;
      // A jitsi room is deployment-scoped: the native id embeds the host for every
      // non-canonical deployment (room@host — jitsi's own XMPP identity shape) so two
      // deployments' same-named rooms never share an identity key. Mirrors the server parser.
      return { platform: "jitsi", native_meeting_id: host === "meet.jit.si" ? room : `${room}@${host}` };
    }
    return null;
  }

  // Bare numeric id → assume Zoom
  if (/^\d{9,11}$/.test(input)) return { platform: "zoom", native_meeting_id: input };

  return null;
}

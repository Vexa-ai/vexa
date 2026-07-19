/** Meeting-link → {platform, native_meeting_id} parsing + validation for the "Add bot" flow.
 *  Id formats mirror the dashboard join-form (clients/dashboard/src/components/join/join-form.tsx):
 *    google_meet → abc-defg-hij   ·   zoom → 9–11 digits   ·   teams → non-empty (passcode handled elsewhere)
 *    telemost → the numeric code from telemost.yandex.ru/j/<id>
 *    jitsi → the meet.jit.si room name, or room@host for a self-hosted deployment (a single
 *    URL-safe path segment; declared VEXA_JITSI_HOSTS arrive via the `jitsiHosts` parameter).
 *  Accepts either a raw id or a full meeting URL the user pasted. */

export type Platform = "google_meet" | "teams" | "zoom" | "jitsi" | "telemost";

export interface ParsedMeeting {
  platform: Platform;
  native_meeting_id: string;
}

const GMEET_ID = /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/;
const ZOOM_ID = /\d{9,11}/;
const TELEMOST_ID = /^\d{10,20}$/;
// A Jitsi room: one URL-safe path segment (no separators/whitespace) — the id is embedded
// back into the construct-URL template, so the encoded form is the id.
const JITSI_ROOM = /^[^/?#\s]+$/;

/** True if `id` is a valid native id for `platform`. */
export function isValidMeetingId(platform: Platform, id: string): boolean {
  const v = id.trim();
  if (!v) return false;
  if (platform === "google_meet") return GMEET_ID.test(v.toLowerCase());
  if (platform === "zoom") return /^\d{9,11}$/.test(v);
  if (platform === "telemost") return TELEMOST_ID.test(v);
  if (platform === "jitsi") return JITSI_ROOM.test(v);
  return v.length > 0; // teams
}

/** Parse a pasted Google Meet / Teams / Zoom / Jitsi / Telemost link (or bare id) into a platform + native id.
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
    if (host.includes("meet.google.com")) {
      const code = url.pathname.split("/").filter(Boolean).pop()?.toLowerCase() ?? "";
      return isValidMeetingId("google_meet", code) ? { platform: "google_meet", native_meeting_id: code } : null;
    }
    if (host.includes("zoom")) {
      const m = url.pathname.match(ZOOM_ID) || url.search.match(ZOOM_ID);
      return m ? { platform: "zoom", native_meeting_id: m[0] } : null;
    }
    if (host.includes("teams.microsoft.com") || host.includes("teams.live.com")) {
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
    if (host === "telemost.yandex.ru") {
      const match = url.pathname.match(/^\/j\/(\d{10,20})\/?$/);
      return match ? { platform: "telemost", native_meeting_id: match[1] } : null;
    }
    // Jitsi: LAST, so every known platform above claims its hosts first (mirrors the server
    // parser's ordering). The canonical public deployment, the deployment-declared hosts
    // (VEXA_JITSI_HOSTS via `jitsiHosts` — same setting the server parser honours), plus the
    // common self-hosted conventions — a host containing "jitsi" (jitsi.example.org) or a
    // "meet" hostname LABEL anywhere (meet.example.org, eu.meet.example.org — jitsi's own
    // recommended naming, regionalized). The room is the path's single segment, kept exactly
    // as pasted (case + percent-encoding preserved — the raw URL rides along as meeting_url,
    // so the bot lands on the right deployment).
    const jitsiHost =
      host === "meet.jit.si" || jitsiHosts.includes(host) ||
      host.includes("jitsi") || host.split(".").includes("meet");
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

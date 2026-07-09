/** Meeting-link → {platform, native_meeting_id} parsing + validation for the "Add bot" flow.
 *  Id formats mirror the dashboard join-form (clients/dashboard/src/components/join/join-form.tsx):
 *    google_meet → abc-defg-hij   ·   zoom → 9–11 digits   ·   teams → non-empty (passcode handled elsewhere).
 *  Accepts either a raw id or a full meeting URL the user pasted. */

export type Platform = "google_meet" | "teams" | "zoom";

export interface ParsedMeeting {
  platform: Platform;
  native_meeting_id: string;
}

const GMEET_ID = /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/;
const ZOOM_ID = /\d{9,11}/;

/** True if `id` is a valid native id for `platform`. */
export function isValidMeetingId(platform: Platform, id: string): boolean {
  const v = id.trim();
  if (!v) return false;
  if (platform === "google_meet") return GMEET_ID.test(v.toLowerCase());
  if (platform === "zoom") return /^\d{9,11}$/.test(v);
  return v.length > 0; // teams
}

/** Parse a pasted Google Meet / Teams / Zoom link (or bare id) into a platform + native id.
 *  Returns null when nothing valid can be extracted. */
export function parseMeetingInput(raw: string): ParsedMeeting | null {
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
    return null;
  }

  // Bare numeric id → assume Zoom
  if (/^\d{9,11}$/.test(input)) return { platform: "zoom", native_meeting_id: input };

  return null;
}

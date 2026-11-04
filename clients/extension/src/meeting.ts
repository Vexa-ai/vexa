/**
 * Platform + native-meeting-id detection from a tab URL — Google Meet, YouTube,
 * Zoom, and MS Teams. Shared by the content script (auto-start trigger) and the
 * background worker (session bootstrap).
 */

export interface MeetingRef {
  platform: 'google_meet' | 'youtube' | 'zoom' | 'teams';
  nativeMeetingId: string;
}

/** True when `hostname` is `domain` itself or a subdomain of it: a label-boundary
 *  match, so a look-alike such as `notyoutube.com` never passes. */
export function onHost(hostname: string, domain: string): boolean {
  return hostname === domain || hostname.endsWith('.' + domain);
}

/** meeting-api's `_TEAMS_THREAD` (collector/meeting_link.py), source for source: a Teams thread id,
 *  the repeat bounded so a scan over caller-supplied text stays linear. */
const TEAMS_THREAD = /19:meeting_[^@%\s/]{1,256}@thread\.v2/i;

/** Python's `urllib.parse.unquote` for what a Teams URL holds: decode %XX, leave a bad escape as is. */
function percentDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s.replace(/%([0-9a-f]{2})/gi, (_m, h: string) => String.fromCharCode(parseInt(h, 16)));
  }
}

export function detectMeeting(url: string): MeetingRef | null {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return null;
  }

  // Google Meet — meet.google.com/abc-defg-hij
  if (onHost(u.hostname, 'meet.google.com')) {
    const seg = u.pathname.split('/').filter(Boolean)[0];
    if (seg && /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/i.test(seg)) {
      return { platform: 'google_meet', nativeMeetingId: seg };
    }
    return null;
  }

  // YouTube — youtube.com/watch?v=ID. The MIXED lane: the tab's <video> is one
  // mixed audio stream the desktop diarizes with pyannote (no per-speaker channels).
  if (onHost(u.hostname, 'youtube.com')) {
    const v = u.searchParams.get('v');
    if (v) return { platform: 'youtube', nativeMeetingId: v };
    return null;
  }

  // Zoom — MIXED lane (offscreen tabCapture → channel 999, diarized by the
  // desktop's mixed pipeline) PLUS speaker-name hints from the zoom-speakers DOM.
  // Native id = the 9-11 digit meeting number. Mirrors the 0.11 server parser
  // (services/meeting-api/meeting_api/schemas.py): /j/<id>, /w/<id>, /wc/join/<id>,
  // and the web-client /wc/<id>/... shape. Hosts include zoom.us subdomains
  // (us02web.zoom.us, app.zoom.us, <company>.zoom.us).
  if (onHost(u.hostname, 'zoom.us')) {
    const parts = u.pathname.split('/').filter(Boolean);
    let id = '';
    if (parts.length >= 2 && (parts[0] === 'j' || parts[0] === 'w')) {
      id = parts[1];                                   // /j/<id>, /w/<id>
    } else if (parts.length >= 3 && parts[0] === 'wc' && parts[1] === 'join') {
      id = parts[2];                                   // /wc/join/<id>
    } else if (parts.length >= 2 && parts[0] === 'wc') {
      id = parts[1];                                   // /wc/<id>/... (web client)
    }
    if (/^\d{9,11}$/.test(id)) return { platform: 'zoom', nativeMeetingId: id };
    return null;
  }

  // MS Teams — MIXED lane (offscreen tabCapture → channel 999, diarized by the
  // desktop's mixed pipeline) PLUS the blue-square speaker hints from the
  // msteams-speakers DOM. Native id = the 10-15 digit meeting number. Mirrors
  // the 0.11 server parser (services/meeting-api/meeting_api/schemas.py
  // parse_meeting_url): teams.live.com/meet/<id> (personal) and the enterprise
  // hosts' /meet/<id> short URL + /v2/...#/meet/<id> deep-link fragment. Hosts:
  // teams.live.com, teams.microsoft.com (+subdomains), teams.cloud.microsoft.
  if (
    onHost(u.hostname, 'teams.live.com') ||
    onHost(u.hostname, 'teams.microsoft.com') ||
    u.hostname === 'teams.cloud.microsoft'
  ) {
    // The classic deep link carries the thread id (…/l/meetup-join/19:meeting_…@thread.v2), and
    // meeting-api keys it by that id (collector/meeting_link.py: searched in the percent-decoded
    // URL, first). Same pattern, same order, so both sides key the meeting alike
    // (vectors: teams-native-id-vectors.json).
    const thread = percentDecode(url).match(TEAMS_THREAD);
    if (thread) return { platform: 'teams', nativeMeetingId: thread[0] };
    // /meet/<10-17 digits> on the path (personal + enterprise short URL) — the lengths meeting-api
    // and the MCP's link parser accept, so a link the server takes is one this recognises…
    let m = u.pathname.match(/^\/meet\/(\d{10,17})\/?$/);
    // …or the enterprise deep-link form /v2/?…#/meet/<id>?p=… (id in the hash).
    if (!m && u.hash) m = u.hash.replace(/^#/, '').match(/^\/meet\/(\d{10,17})\b/);
    if (m) return { platform: 'teams', nativeMeetingId: m[1] };
    return null;
  }

  return null;
}

/** True when the URL is a known MIXED-lane host (whole-tab audio), regardless of
 *  whether the meeting id is in the URL yet. Used to mint the tabCapture stream on
 *  the toolbar click for hosts like teams.cloud.microsoft that expose the id only
 *  via the DOM (the content script reports the thread id separately). */
export function isMixedHost(url: string): boolean {
  try {
    const h = new URL(url).hostname;
    return onHost(h, 'youtube.com') || onHost(h, 'zoom.us')
      || onHost(h, 'teams.microsoft.com') || onHost(h, 'teams.live.com') || h === 'teams.cloud.microsoft';
  } catch {
    return false;
  }
}

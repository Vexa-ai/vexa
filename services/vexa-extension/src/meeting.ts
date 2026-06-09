/**
 * Platform + native-meeting-id detection from a tab URL.
 * Shared by the content script (auto-start trigger) and the background worker
 * (session bootstrap). Add a platform here to support it everywhere.
 */

export interface MeetingRef {
  platform: 'google_meet' | 'zoom' | 'teams';
  nativeMeetingId: string;
}

export function detectMeeting(url: string): MeetingRef | null {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return null;
  }

  // Google Meet — meet.google.com/abc-defg-hij
  if (u.hostname.endsWith('meet.google.com')) {
    const seg = u.pathname.split('/').filter(Boolean)[0];
    if (seg && /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/i.test(seg)) {
      return { platform: 'google_meet', nativeMeetingId: seg };
    }
    return null;
  }

  // Zoom Web Client — app.zoom.us/wc/<id>/join  or  app.zoom.us/wc/join/<id>
  // (the numeric meeting id is what meeting-api expects for zoom).
  if (u.hostname.endsWith('zoom.us')) {
    const m = u.pathname.match(/\/wc\/(?:join\/)?(\d{9,12})/);
    if (m) return { platform: 'zoom', nativeMeetingId: m[1] };
    return null;
  }

  // Microsoft Teams — teams.live.com/meet/<id> (consumer) or
  // teams.microsoft.com/meet/<id> (enterprise short URL). meeting-api expects
  // the 10-15 digit numeric id (Platform.construct_meeting_url).
  if (u.hostname.endsWith('teams.live.com') || u.hostname.endsWith('teams.microsoft.com')) {
    const m = u.pathname.match(/\/meet\/(\d{10,15})/);
    if (m) return { platform: 'teams', nativeMeetingId: m[1] };
    return null;
  }

  return null;
}

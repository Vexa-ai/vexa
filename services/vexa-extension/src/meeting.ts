/**
 * Platform + native-meeting-id detection from a tab URL.
 * Shared by the content script (auto-start trigger) and the background worker
 * (session bootstrap). Add a platform here to support it everywhere.
 */

export interface MeetingRef {
  platform: 'google_meet' | 'zoom' | 'teams';
  nativeMeetingId: string;
}

/**
 * Media tabs usable as a transcription-debug source (YouTube). Deliberately
 * SEPARATE from detectMeeting so auto-start never fires on normal browsing —
 * capture on these is toolbar-click only. Rides the zoom tab-capture flow
 * (mixed single-channel → ingest's ChunkedTranscriber); the platform is
 * reported as 'zoom' because meeting-api's Platform enum has no media kind
 * and the WS contract stays frozen.
 */
export function detectMediaTab(url: string): MeetingRef | null {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (u.hostname === 'www.youtube.com' || u.hostname === 'youtube.com' || u.hostname === 'm.youtube.com') {
    const v = u.searchParams.get('v') || u.pathname.match(/^\/(?:shorts|live)\/([\w-]{6,})/)?.[1];
    if (v) return { platform: 'zoom', nativeMeetingId: `yt-${v}` };
    return null;
  }
  if (u.hostname === 'youtu.be') {
    const v = u.pathname.split('/').filter(Boolean)[0];
    if (v) return { platform: 'zoom', nativeMeetingId: `yt-${v}` };
  }
  return null;
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
  if (isTeamsHost(u.hostname)) {
    const m = u.pathname.match(/\/meet\/(\d{10,15})/);
    if (m) return { platform: 'teams', nativeMeetingId: m[1] };
    return null;
  }

  return null;
}

/**
 * Teams web app hosts. The new Teams client (teams.cloud.microsoft, and the
 * /v2/ SPA on the classic hosts) never carries the meeting id in the URL —
 * being "in a meeting" must be detected from the DOM (hangup button), with a
 * synthesized native id. The content script owns that flow.
 */
export function isTeamsHost(hostname: string): boolean {
  return hostname.endsWith('teams.live.com')
    || hostname.endsWith('teams.microsoft.com')
    || hostname === 'teams.cloud.microsoft';
}

/**
 * In-meeting indicators for the Teams web client — the same hangup/Leave
 * button selectors the bot's admission check uses
 * (vexa-bot/core/src/platforms/msteams/selectors.ts).
 */
export const TEAMS_IN_MEETING_SELECTORS = [
  'button[id="hangup-button"]',
  'button[data-tid="hangup-main-btn"]',
  'button[aria-label="Leave"]',
  '[role="toolbar"] button[aria-label*="Leave"]',
];

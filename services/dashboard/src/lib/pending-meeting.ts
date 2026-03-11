const PENDING_MEETING_KEY = "vexa.pending_meeting_url";
const PENDING_MEETING_TS_KEY = "vexa.pending_meeting_ts";
const MAX_AGE_MS = 15 * 60 * 1000; // 15 minutes (matches magic link TTL)

export function savePendingMeetingUrl(meetingUrl: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(PENDING_MEETING_KEY, meetingUrl);
  localStorage.setItem(PENDING_MEETING_TS_KEY, Date.now().toString());
}

export function consumePendingMeetingUrl(): string | null {
  if (typeof window === "undefined") return null;
  const url = localStorage.getItem(PENDING_MEETING_KEY);
  const ts = localStorage.getItem(PENDING_MEETING_TS_KEY);

  // Always clean up
  localStorage.removeItem(PENDING_MEETING_KEY);
  localStorage.removeItem(PENDING_MEETING_TS_KEY);

  if (!url || !ts) return null;

  // Check TTL
  const age = Date.now() - parseInt(ts, 10);
  if (age > MAX_AGE_MS) return null;

  return url;
}

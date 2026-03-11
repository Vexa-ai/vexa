import type { Platform } from "@/types/vexa";

export interface ParsedMeetingInput {
  platform: Platform;
  meetingId: string;
  passcode?: string;
}

// Parse Google Meet, Zoom, or Teams URL/meeting ID
export function parseMeetingInput(input: string): ParsedMeetingInput | null {
  const trimmed = input.trim();
  if (!trimmed) return null;
  // Google Meet URL patterns
  // https://meet.google.com/abc-defg-hij
  // meet.google.com/abc-defg-hij
  const googleMeetUrlRegex = /(?:https?:\/\/)?meet\.google\.com\/([a-z]{3}-[a-z]{4}-[a-z]{3})/i;
  const googleMeetMatch = trimmed.match(googleMeetUrlRegex);
  if (googleMeetMatch) {
    return { platform: "google_meet", meetingId: googleMeetMatch[1].toLowerCase() };
  }

  // Direct Google Meet code (abc-defg-hij)
  const googleMeetCodeRegex = /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/i;
  if (googleMeetCodeRegex.test(trimmed)) {
    return { platform: "google_meet", meetingId: trimmed.toLowerCase() };
  }

  // Microsoft Teams URL patterns
  // https://teams.microsoft.com/l/meetup-join/...
  // https://teams.live.com/meet/9387167464734?p=qxJanYOcdjN4d6UlGa
  const teamsUrlRegex = /(?:https?:\/\/)?(?:teams\.microsoft\.com|teams\.live\.com)\/(?:l\/meetup-join|meet)\/([^\s?#]+)/i;
  const teamsMatch = trimmed.match(teamsUrlRegex);
  if (teamsMatch) {
    // Extract meeting ID and passcode from the URL
    const meetingPath = teamsMatch[1];
    // URL decode and extract the meeting thread id
    const decodedPath = decodeURIComponent(meetingPath);
    const meetingId = decodedPath.split('/')[0] || decodedPath;

    // Extract passcode from query parameter (p=...)
    const passcodeMatch = trimmed.match(/[?&]p=([^&]+)/i);
    const passcode = passcodeMatch ? decodeURIComponent(passcodeMatch[1]) : undefined;

    return { platform: "teams", meetingId, passcode };
  }

  // Zoom URL patterns
  // https://zoom.us/j/85173157171?pwd=xxx
  // https://us05web.zoom.us/j/85173157171?pwd=xxx
  const zoomUrlRegex = /(?:https?:\/\/)?(?:[\w-]+\.)?zoom\.us\/j\/(\d+)/i;
  const zoomMatch = trimmed.match(zoomUrlRegex);
  if (zoomMatch) {
    const passcodeMatch = trimmed.match(/[?&]pwd=([^&]+)/i);
    const passcode = passcodeMatch ? decodeURIComponent(passcodeMatch[1]) : undefined;
    return { platform: "zoom", meetingId: zoomMatch[1], passcode };
  }

  // Zoom meeting ID (9-11 digits)
  if (/^\d{9,11}$/.test(trimmed)) {
    return { platform: "zoom", meetingId: trimmed };
  }

  // Teams meeting ID (longer numeric strings)
  if (/^\d{12,}$/.test(trimmed)) {
    return { platform: "teams", meetingId: trimmed };
  }

  // Generic Teams detection - contains teams.microsoft.com
  if (trimmed.toLowerCase().includes('teams.microsoft.com') || trimmed.toLowerCase().includes('teams.live.com')) {
    // Try to extract any usable ID
    const genericId = trimmed.replace(/^https?:\/\//, '').split('/').pop()?.split('?')[0];
    if (genericId) {
      // Also try to extract passcode from query string
      const passcodeMatch = trimmed.match(/[?&]p=([^&]+)/i);
      const passcode = passcodeMatch ? decodeURIComponent(passcodeMatch[1]) : undefined;
      return { platform: "teams", meetingId: genericId, passcode };
    }
  }

  return null;
}

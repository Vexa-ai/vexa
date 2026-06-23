/**
 * parse-meeting-input.ts — turn a pasted meeting URL (or bare id) into (platform, native id).
 *
 * Pure function, zero deps, no DOM. This is the cleaned-up version of the vendored dashboard's
 * `lib/parse-meeting-input.ts` behavior: detect the platform from a Google Meet / Zoom / Teams URL,
 * extract the native meeting id, and pull a passcode out of the query string when one is present.
 * Bare ids (a Meet code, a 9–11 digit Zoom id, a long Teams numeric id) are accepted directly.
 *
 * `platform` here is the dash-contracts vocabulary, so the parse result drops straight into a
 * `CreateBotRequest`. Returns `null` when the input isn't recognizably a meeting link/id.
 */
import type { Platform } from "./types.js";

export interface ParsedMeetingInput {
  /** Detected platform (dash-contracts vocabulary). */
  platform: Platform;
  /** The extracted native meeting id (e.g. "abc-defg-hij", "85173157171"). */
  nativeId: string;
  /** Passcode pulled from the URL query string, when present. */
  passcode?: string;
  /** The original URL, preserved for Teams/white-label links the bot must join verbatim. */
  originalUrl?: string;
}

export function parseMeetingInput(input: string): ParsedMeetingInput | null {
  const trimmed = input.trim();
  if (!trimmed) return null;

  // ── Google Meet ───────────────────────────────────────────────────────────────────────────────
  // https://meet.google.com/abc-defg-hij  ·  meet.google.com/abc-defg-hij
  const gmeetUrl = trimmed.match(
    /(?:https?:\/\/)?meet\.google\.com\/([a-z]{3}-[a-z]{4}-[a-z]{3})/i,
  );
  if (gmeetUrl) {
    return { platform: "google_meet", nativeId: gmeetUrl[1].toLowerCase() };
  }
  // bare Meet code: abc-defg-hij
  if (/^[a-z]{3}-[a-z]{4}-[a-z]{3}$/i.test(trimmed)) {
    return { platform: "google_meet", nativeId: trimmed.toLowerCase() };
  }

  // ── Microsoft Teams ─────────────────────────────────────────────────────────────────────────────
  // https://teams.microsoft.com/l/meetup-join/...  ·  https://teams.live.com/meet/9387167464734?p=...
  const teamsUrl = trimmed.match(
    /(?:https?:\/\/)?(?:teams\.microsoft\.com|teams\.live\.com)\/(?:l\/meetup-join|meet)\/([^\s?#]+)/i,
  );
  if (teamsUrl) {
    const decoded = decodeURIComponent(teamsUrl[1]);
    const nativeId = decoded.split("/")[0] || decoded;
    const passcode = extractParam(trimmed, "p");
    const originalUrl = trimmed.startsWith("http") ? trimmed : `https://${trimmed}`;
    return { platform: "teams", nativeId, passcode, originalUrl };
  }

  // ── Zoom ────────────────────────────────────────────────────────────────────────────────────────
  // https://zoom.us/j/85173157171?pwd=xxx  ·  https://us05web.zoom.us/j/85173157171?pwd=xxx
  const zoomUrl = trimmed.match(/(?:https?:\/\/)?(?:[\w-]+\.)?zoom\.us\/j\/(\d+)/i);
  if (zoomUrl) {
    const passcode = extractParam(trimmed, "pwd");
    // Zoom REQUIRES meeting_url on POST /bots (the backend 422s a zoom request without it — the join
    // needs the full link, incl. its pwd, not just the numeric id). Preserve the original URL so the
    // form sends it as meeting_url, exactly as the Teams branch does.
    const originalUrl = trimmed.startsWith("http") ? trimmed : `https://${trimmed}`;
    return { platform: "zoom", nativeId: zoomUrl[1], passcode, originalUrl };
  }

  // ── bare numeric ids ─────────────────────────────────────────────────────────────────────────────
  // 9–11 digits → Zoom; 12+ digits → Teams
  if (/^\d{9,11}$/.test(trimmed)) return { platform: "zoom", nativeId: trimmed };
  if (/^\d{12,}$/.test(trimmed)) return { platform: "teams", nativeId: trimmed };

  return null;
}

/** Read a query-string param (case-insensitive key) out of a raw URL string, URL-decoded. */
function extractParam(url: string, key: string): string | undefined {
  const m = url.match(new RegExp(`[?&]${key}=([^&\\s]+)`, "i"));
  return m ? decodeURIComponent(m[1]) : undefined;
}

/** meetingSendUrl — the ONE decision every "send the bot to this row" site makes.
 *
 *  `POST /bots` needs an ADDRESS for the meeting, and there are exactly two ways to have one:
 *  the caller carries an explicit `meeting_url`, or the server can construct one from the bare
 *  native id. Which platforms the server can construct for is NOT a UI preference — it is stated
 *  in meeting-api and enforced there:
 *
 *    • `bot_spawn/service.py` `_URL_TEMPLATES` — `google_meet` only.
 *    • `bot_spawn/service.py` `_teams_url` — `teams`, both id shapes, always a URL.
 *    • `bot_spawn/service.py` `construct_meeting_url` — `zoom` and `jitsi` → `None`, deliberately.
 *      A jitsi room name is scoped to a DEPLOYMENT; a zoom id alone is not an address, and the
 *      function is PASSCODE-FREE by contract, so it must not invent one. Its own comment names the
 *      consequence: those callers "pass an explicit `meeting_url` … the UI, MCP, and calendar
 *      paths all carry it."
 *    • `bot_spawn/router.py` — `if not meeting_url and construct_meeting_url(...) is None:` → 422
 *      "unsupported platform 'zoom' without a meeting_url".
 *
 *  The UI used to carry it only by luck: each send site inlined its own `m.meeting_url ? … :
 *  google_meet ? … : {}`, and offered the action on `native_id` alone. A zoom row whose stored link
 *  is absent therefore produced a request the API MUST refuse, with wording aimed at an API caller
 *  ("use google_meet/teams") that a dashboard user cannot act on (#1681).
 *
 *  So the decision lives here, once, and it answers the whole question — not just "which URL" but
 *  "is this row addressable at all", which is what the button state needs to be honest.
 */

/** Platforms whose BARE native id is not an address: meeting-api has no template for them, so a
 *  spawn without an explicit `meeting_url` is refused. Mirrors `construct_meeting_url` returning
 *  `None`. Keep in step with it — a new platform without a template belongs in this set. */
export const URL_REQUIRED_PLATFORMS: ReadonlySet<string> = new Set(["zoom", "jitsi"]);

/** The model stores platform DISPLAY-cased ("Google Meet", else the raw API slug like
 *  "teams"/"zoom"). Every endpoint wants the slug. */
export function platformSlug(platform: string): string {
  return platform === "Google Meet" ? "google_meet" : platform.toLowerCase().replace(/\s+/g, "_");
}

/** Whether this platform needs the caller to carry the URL (the server cannot construct one). */
export function requiresExplicitMeetingUrl(slug: string): boolean {
  return URL_REQUIRED_PLATFORMS.has(slug);
}

/** Human-readable platform name for a refusal message ("This Zoom meeting…"). */
function platformLabel(slug: string): string {
  if (slug === "google_meet") return "Google Meet";
  if (slug === "zoom") return "Zoom";
  if (slug === "jitsi") return "Jitsi";
  if (slug === "teams") return "Teams";
  return slug;
}

export type SendAddress =
  /** The row is addressable. `meeting_url` is present only when the request must carry it. */
  | { ok: true; meeting_url?: string }
  /** The row is NOT addressable — the send would be refused by the API. `reason` is what the
   *  surface that offered the action tells the user, in terms they can act on. */
  | { ok: false; reason: string };

/** Resolve the address for a `POST /bots` on an existing row.
 *
 *  Order, and why:
 *   1. The row's STORED link wins whenever it exists — it is the link the user or the calendar
 *      actually gave us, credentials and query string intact. Never re-derive over it.
 *   2. `google_meet` has a canonical form from the bare code, so a Meet row without a stored link
 *      is still addressable client-side (this preserves the pre-existing fallback).
 *   3. `teams` is constructible SERVER-side from either id shape, so we send no URL and let
 *      meeting-api build it (it knows the deployment's Teams host; we do not).
 *   4. Anything in `URL_REQUIRED_PLATFORMS` with no stored link is NOT addressable. We say so here
 *      instead of firing a request whose only possible outcome is a 422.
 */
export function resolveSendAddress(
  slug: string,
  nativeId: string | undefined,
  storedUrl: string | undefined,
): SendAddress {
  const stored = storedUrl?.trim();
  if (stored) return { ok: true, meeting_url: stored };
  if (!nativeId) {
    return { ok: false, reason: "This meeting has no link yet — add the meeting link, then send the bot." };
  }
  if (slug === "google_meet") return { ok: true, meeting_url: `https://meet.google.com/${nativeId}` };
  if (requiresExplicitMeetingUrl(slug)) {
    return {
      ok: false,
      reason:
        `This ${platformLabel(slug)} meeting has no stored link, and a ${platformLabel(slug)} bot ` +
        `cannot be addressed by meeting id alone. Add the full invite link to this meeting, then send the bot.`,
    };
  }
  return { ok: true };
}

/** The `meeting_url` field for a request body — `{}` when the request carries none.
 *  Spread into the body: `{ platform, native_meeting_id, ...meetingUrlField(addr) }`. */
export function meetingUrlField(addr: SendAddress): { meeting_url?: string } {
  return addr.ok && addr.meeting_url ? { meeting_url: addr.meeting_url } : {};
}

/**
 * golden.js — the single source of truth for the L4 fixture's input and expected output.
 *
 * Shared by BOTH sides of the gate so they can never drift:
 *   • the fixture page (form-entry.tsx → mounts JoinForm with GOLDEN_DEFAULT_BOT_NAME) imports this
 *   • the spec (join-form.spec.ts) imports this and asserts the onSubmit payload equals GOLDEN_REQUEST
 *
 * The flow under test: the user pastes GOLDEN_URL into the meeting input and submits. The component's
 * parser must detect platform "google_meet" and native id "abc-defg-hij" and call onSubmit with
 * GOLDEN_REQUEST. The default bot name is pre-filled and must ride along on the request.
 */

/** Pre-fills the bot-name field (the `defaultBotName` prop). */
export const GOLDEN_DEFAULT_BOT_NAME = "Vexa";

/** What the user types into the meeting URL/ID input. */
export const GOLDEN_URL = "https://meet.google.com/abc-defg-hij";

/** The exact CreateBotRequest the component must hand to onSubmit for GOLDEN_URL. */
export const GOLDEN_REQUEST = {
  platform: "google_meet",
  native_meeting_id: "abc-defg-hij",
  bot_name: "Vexa",
  // DF1 — the form now always sends the explicit recording/transcription toggles (default on), so the
  // submitted request truthfully reflects what the bot will do.
  recording_enabled: true,
  transcribe_enabled: true,
};

/**
 * @vexa/join — the isolated meeting joining layer (Google Meet + MS Teams + Zoom web client + Jitsi Meet).
 *
 * Public surface. Everything below imports only from within this package
 * (verify with `npm run check:isolation`). The embedder supplies a Page and
 * observes state through hooks; recording, transcription, Redis, and the
 * meeting-api callbacks all live OUTSIDE this boundary.
 */
import type { Page } from "playwright";
import { joinGoogleMeeting } from "./googlemeet/join";
import { waitForGoogleMeetingAdmission, checkForGoogleAdmissionSilent } from "./googlemeet/admission";
import { prepareForRecording, leaveGoogleMeet } from "./googlemeet/leave";
import { startGoogleRemovalMonitor } from "./googlemeet/removal";
import { joinMicrosoftTeams } from "./msteams/join";
import { waitForTeamsMeetingAdmission, checkForTeamsAdmissionSilent } from "./msteams/admission";
import { prepareForRecording as prepareForTeamsRecording, leaveMicrosoftTeams } from "./msteams/leave";
import { startTeamsRemovalMonitor } from "./msteams/removal";
import { joinZoomMeeting, buildZoomWebClientUrl } from "./zoom/join";
import { waitForZoomMeetingAdmission, checkForZoomAdmissionSilent } from "./zoom/admission";
import { leaveZoomMeeting, dismissZoomPopups } from "./zoom/leave";
import { startZoomRemovalMonitor } from "./zoom/removal";
import { joinJitsiMeeting, buildJitsiMeetingUrl } from "./jitsi/join";
import { waitForJitsiMeetingAdmission, checkForJitsiAdmissionSilent } from "./jitsi/admission";
import { leaveJitsiMeeting } from "./jitsi/leave";
import { startJitsiRemovalMonitor } from "./jitsi/removal";
import { startDebugView } from "./shared/escalation";
import { setHooks, type BotConfig, type Hooks, type JoinState } from "./_host";
import { JOIN_BROWSER_ARGS, getJoinBrowserArgs } from "./browser-args";

export type { BotConfig, Hooks, JoinState };
export { startDebugView, setHooks };
// Canonical browser launch args — the vexa-bot service and the debug harness both
// build on this ONE set (browser-args.ts), so join↔bot flags never drift.
export { JOIN_BROWSER_ARGS, getJoinBrowserArgs };

export type Platform = "google_meet" | "teams" | "zoom" | "jitsi";

export interface JoinResult {
  admitted: boolean;
  state: JoinState;
}

export interface JoinOptions {
  meetingUrl: string;
  /** which platform's join flow to run; default: inferred from meetingUrl */
  platform?: Platform;
  botName?: string;
  /** meeting passcode — filled into zoom's passcode screen / jitsi's room-password prompt */
  passcode?: string;
  /** force "humanized" (X11) or "synthetic" (CDP) input; default: humanized for gmeet */
  uiInteractionMode?: "humanized" | "synthetic";
  /** join as a signed-in user — caller hands in a persistent, logged-in context
   *  (e.g. from @vexa/remote-browser); the brick skips guest name-entry. */
  authenticated?: boolean;
  waitingRoomTimeoutMs?: number;
  /** turn on the live debug view (VNC pixels on Linux, CDP control anywhere) */
  debug?: boolean;
  hooks?: Partial<Hooks>;
}

/** Infer the platform from the meeting URL. Throws on an unrecognized host. */
export function resolvePlatform(meetingUrl: string): Platform {
  if (meetingUrl.includes("meet.google.com")) return "google_meet";
  if (meetingUrl.includes("teams.microsoft.com") || meetingUrl.includes("teams.live.com")) return "teams";
  // Canonical zoom.us / *.zoom.us only — white-label portals (LFX etc.) can't be
  // inferred from the URL; the embedder passes platform: "zoom" explicitly.
  // Same rule for Jitsi: only the canonical public deployments are inferable —
  // a self-hosted Jitsi lives on an arbitrary host, so the embedder passes
  // platform: "jitsi" explicitly.
  try {
    const host = new URL(meetingUrl).hostname;
    if (host === "zoom.us" || host.endsWith(".zoom.us")) return "zoom";
    if (host === "meet.jit.si" || host === "8x8.vc" || host.endsWith(".8x8.vc")) return "jitsi";
  } catch { /* fall through to throw below */ }
  throw new Error(`Cannot infer platform from meeting URL: ${meetingUrl}`);
}

/**
 * Drive a meeting join to its admission verdict on the page you hand in.
 * Returns once admitted, rejected, or timed out. Does NOT record or transcribe.
 */
/** Default bot name, reads env at call time so tests and the server-side proxy observe it when set. */
export function defaultBotName(): string {
  return process.env.DEFAULT_BOT_NAME?.trim() || "Vexa Join Layer";
}

export async function joinMeeting(page: Page, opts: JoinOptions): Promise<JoinResult> {
  if (opts.hooks) setHooks(opts.hooks);

  const platform = opts.platform ?? resolvePlatform(opts.meetingUrl);
  const botConfig: BotConfig = {
    platform,
    botName: opts.botName ?? defaultBotName(),
    passcode: opts.passcode,
    authenticated: opts.authenticated,
    uiInteractionMode: opts.uiInteractionMode,
    automaticLeave: { waitingRoomTimeout: opts.waitingRoomTimeoutMs ?? 180_000 },
  };

  let debugInfo;
  if (opts.debug) {
    debugInfo = await startDebugView();
    setHooks({}); // ensure default state-logger is installed if none supplied
  }

  let admitted: boolean;
  if (platform === "teams") {
    await joinMicrosoftTeams(page, opts.meetingUrl, botConfig.botName!, botConfig);
    admitted = await waitForTeamsMeetingAdmission(
      page, botConfig.automaticLeave!.waitingRoomTimeout, botConfig,
    );
  } else if (platform === "zoom") {
    await joinZoomMeeting(page, opts.meetingUrl, botConfig.botName!, botConfig);
    admitted = await waitForZoomMeetingAdmission(
      page, botConfig.automaticLeave!.waitingRoomTimeout, botConfig,
    );
  } else if (platform === "jitsi") {
    await joinJitsiMeeting(page, opts.meetingUrl, botConfig.botName!, botConfig);
    admitted = await waitForJitsiMeetingAdmission(
      page, botConfig.automaticLeave!.waitingRoomTimeout, botConfig,
    );
  } else if (platform === "google_meet") {
    await joinGoogleMeeting(page, opts.meetingUrl, botConfig.botName!, botConfig);
    admitted = await waitForGoogleMeetingAdmission(
      page, botConfig.automaticLeave!.waitingRoomTimeout, botConfig,
    );
  } else {
    // Explicit refusal, never a fallthrough: an unknown platform used to silently run the GOOGLE
    // MEET join flow against whatever URL it was handed — the wrong flow on the wrong site, failing
    // minutes later with misattributed selector errors instead of naming the real problem here.
    throw new Error(
      `Unsupported platform '${platform}' — this join layer drives google_meet, teams, zoom, jitsi`,
    );
  }

  return { admitted: !!admitted, state: admitted ? "admitted" : "awaiting_admission" };
}

export { joinGoogleMeeting, waitForGoogleMeetingAdmission, checkForGoogleAdmissionSilent, prepareForRecording, leaveGoogleMeet, startGoogleRemovalMonitor };
export { startGoogleAlonenessMonitor } from "./googlemeet/removal";
// AdmissionError carries a TYPED `outcome` (denial / lobby_timeout / join_failure / auth_session_missing).
// It is THROWN by the join/admission path; the JoinDriver adapter catches it and maps the outcome → a
// JoinOutcome so a host DENIAL is recorded as a permanent `rejected` — and a signed-out profile
// (AuthSessionError, an AdmissionError subclass) as the permanent `auth_session_missing` — never
// collapsed into a transient (retried) `join_failure` (G1).
export { AdmissionError } from "./shared/admission";
export type { AdmissionOutcome } from "./shared/admission";
export { AuthSessionError } from "./googlemeet/join";
export { joinMicrosoftTeams, waitForTeamsMeetingAdmission, checkForTeamsAdmissionSilent, prepareForTeamsRecording, leaveMicrosoftTeams, startTeamsRemovalMonitor };
export { joinZoomMeeting, buildZoomWebClientUrl, waitForZoomMeetingAdmission, checkForZoomAdmissionSilent, leaveZoomMeeting, dismissZoomPopups, startZoomRemovalMonitor };
export { joinJitsiMeeting, buildJitsiMeetingUrl, waitForJitsiMeetingAdmission, checkForJitsiAdmissionSilent, leaveJitsiMeeting, startJitsiRemovalMonitor };

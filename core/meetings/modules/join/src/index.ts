/**
 * @vexa/join — the isolated meeting joining layer (Google Meet + MS Teams + Zoom web client).
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
import { startDebugView } from "./shared/escalation";
import { setHooks, type BotConfig, type Hooks, type JoinState } from "./_host";
import { JOIN_BROWSER_ARGS, getJoinBrowserArgs } from "./browser-args";

export type { BotConfig, Hooks, JoinState };
export { startDebugView, setHooks };
// Canonical browser launch args — the vexa-bot service and the debug harness both
// build on this ONE set (browser-args.ts), so join↔bot flags never drift.
export { JOIN_BROWSER_ARGS, getJoinBrowserArgs };

export type Platform = "google_meet" | "teams" | "zoom";

export interface JoinResult {
  admitted: boolean;
  state: JoinState;
}

export interface JoinOptions {
  meetingUrl: string;
  /** which platform's join flow to run; default: inferred from meetingUrl */
  platform?: Platform;
  botName?: string;
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
  try {
    const host = new URL(meetingUrl).hostname;
    if (host === "zoom.us" || host.endsWith(".zoom.us")) return "zoom";
  } catch { /* fall through to throw below */ }
  throw new Error(`Cannot infer platform from meeting URL: ${meetingUrl}`);
}

/**
 * Drive a meeting join to its admission verdict on the page you hand in.
 * Returns once admitted, rejected, or timed out. Does NOT record or transcribe.
 */
export async function joinMeeting(page: Page, opts: JoinOptions): Promise<JoinResult> {
  if (opts.hooks) setHooks(opts.hooks);

  const platform = opts.platform ?? resolvePlatform(opts.meetingUrl);
  const botConfig: BotConfig = {
    platform,
    botName: opts.botName ?? "Vexa Join Layer",
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
  } else {
    await joinGoogleMeeting(page, opts.meetingUrl, botConfig.botName!, botConfig);
    admitted = await waitForGoogleMeetingAdmission(
      page, botConfig.automaticLeave!.waitingRoomTimeout, botConfig,
    );
  }

  return { admitted: !!admitted, state: admitted ? "admitted" : "awaiting_admission" };
}

export { joinGoogleMeeting, waitForGoogleMeetingAdmission, checkForGoogleAdmissionSilent, prepareForRecording, leaveGoogleMeet, startGoogleRemovalMonitor };
// AdmissionError carries a TYPED `outcome` (denial / lobby_timeout / join_failure). It is THROWN by the
// admission wait; the JoinDriver adapter catches it and maps the outcome → a JoinOutcome so a host DENIAL
// is recorded as a permanent `rejected`, not collapsed into a transient (retried) `join_failure` (G1).
export { AdmissionError } from "./googlemeet/admission";
export type { AdmissionOutcome } from "./googlemeet/admission";
export { joinMicrosoftTeams, waitForTeamsMeetingAdmission, checkForTeamsAdmissionSilent, prepareForTeamsRecording, leaveMicrosoftTeams, startTeamsRemovalMonitor };
export { joinZoomMeeting, buildZoomWebClientUrl, waitForZoomMeetingAdmission, checkForZoomAdmissionSilent, leaveZoomMeeting, dismissZoomPopups, startZoomRemovalMonitor };

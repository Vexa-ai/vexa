/**
 * @vexa/meet-join — the isolated Google Meet joining layer.
 *
 * Public surface. Everything below imports only from within this package
 * (verify with `npm run check:isolation`). The embedder supplies a Page and
 * observes state through hooks; recording, transcription, Redis, and the
 * meeting-api callbacks all live OUTSIDE this boundary.
 */
import type { Page } from "playwright";
import { joinGoogleMeeting } from "./googlemeet/join";
import { waitForGoogleMeetingAdmission } from "./googlemeet/admission";
import { leaveGoogleMeet } from "./googlemeet/leave";
import { startGoogleRemovalMonitor } from "./googlemeet/removal";
import { startDebugView } from "./shared/escalation";
import { setHooks, type BotConfig, type Hooks, type JoinState } from "./_host";

export type { BotConfig, Hooks, JoinState };
export { startDebugView };

export interface JoinResult {
  admitted: boolean;
  state: JoinState;
}

export interface JoinOptions {
  meetingUrl: string;
  botName?: string;
  /** force "humanized" (X11) or "synthetic" (CDP) input; default: humanized for gmeet */
  uiInteractionMode?: "humanized" | "synthetic";
  waitingRoomTimeoutMs?: number;
  /** turn on the live debug view (VNC pixels on Linux, CDP control anywhere) */
  debug?: boolean;
  hooks?: Partial<Hooks>;
}

/**
 * Drive a Google Meet join to its admission verdict on the page you hand in.
 * Returns once admitted, rejected, or timed out. Does NOT record or transcribe.
 */
export async function joinMeeting(page: Page, opts: JoinOptions): Promise<JoinResult> {
  if (opts.hooks) setHooks(opts.hooks);

  const botConfig: BotConfig = {
    platform: "google_meet",
    botName: opts.botName ?? "Vexa Join Layer",
    uiInteractionMode: opts.uiInteractionMode,
    automaticLeave: { waitingRoomTimeout: opts.waitingRoomTimeoutMs ?? 180_000 },
  };

  let debugInfo;
  if (opts.debug) {
    debugInfo = await startDebugView();
    setHooks({}); // ensure default state-logger is installed if none supplied
  }

  await joinGoogleMeeting(page, opts.meetingUrl, botConfig.botName!, botConfig);

  const admitted = await waitForGoogleMeetingAdmission(
    page, botConfig.automaticLeave!.waitingRoomTimeout, botConfig,
  );

  return { admitted: !!admitted, state: admitted ? "admitted" : "awaiting_admission" };
}

export { joinGoogleMeeting, waitForGoogleMeetingAdmission, leaveGoogleMeet, startGoogleRemovalMonitor };

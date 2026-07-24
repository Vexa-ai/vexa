/**
 * JoinDriver adapter (2b) — wraps @vexa/join (the platform join/admission/removal/leave brick)
 * behind the orchestrator's JoinDriver port. ALL platform/DOM knowledge stays in the brick; this
 * only maps @vexa/join's JoinState → lifecycle.v1 BotStatus and routes the per-platform leave/
 * removal. The orchestrator never imports @vexa/join — only this adapter does. (Ported from
 * services/vexa-bot_new/src/adapters/join-vexa.ts onto the v0.12 ports/contracts.)
 */
import type { Page } from '@vexa/remote-browser';
import {
  joinMeeting,
  AdmissionError,
  leaveGoogleMeet, leaveMicrosoftTeams, leaveZoomMeeting, leaveJitsiMeeting,
  startGoogleRemovalMonitor, startTeamsRemovalMonitor, startZoomRemovalMonitor, startJitsiRemovalMonitor,
  type JoinState, type Platform as JoinPlatform, type AdmissionOutcome,
} from '@vexa/join';
import type { BotStatus } from './contracts.js';
import type { Invocation } from './config.js';
import type { JoinDriver, JoinOutcome, JoinResult } from './ports.js';

/**
 * Map @vexa/join's typed AdmissionError `outcome` → a JoinOutcome (G1).
 *
 * The admission wait THROWS an `AdmissionError` carrying a precise `outcome`; without this the
 * orchestrator's catch blanket-maps every throw to a transient `join_failure` → the retry classifier
 * (`lifecycle/retry.py`) RE-SPAWNS a bot that was actually DENIED, burning quota. Mapping the outcome
 * keeps the truth: a `denial` → `rejected` → `awaiting_admission_rejected` (PERMANENT, no retry); a
 * `lobby_timeout` → `timeout` → `awaiting_admission_timeout` (transient, a legit retry); a `join_failure`
 * stays `error` → `join_failure` (transient); an `auth_session_missing` (signed-out profile in
 * authenticated mode) → `auth_missing` → `auth_session_missing` (PERMANENT — a re-spawn against a dead
 * profile can never succeed). NB: a distinct `blocked` reason needs a sealed-contract
 * `CompletionReason` value (lane:contract) — until then a detected block surfaces via this same path.
 */
export function admissionOutcomeToJoinOutcome(outcome: AdmissionOutcome): JoinOutcome {
  switch (outcome) {
    case 'denial':               return 'rejected';
    case 'lobby_timeout':        return 'timeout';
    case 'auth_session_missing': return 'auth_missing';
    case 'join_failure':         return 'error';
    default:                     return 'error';
  }
}

/** @vexa/join JoinState → lifecycle.v1 BotStatus (null = not a bot-status transition). */
function mapState(s: JoinState): BotStatus | null {
  switch (s) {
    case 'awaiting_admission': return 'awaiting_admission';
    case 'admitted':           return 'active';
    case 'blocked':
    case 'needs_human_help':   return 'needs_help';
    default:                   return null;   // 'joining'/'leaving' — orchestrator owns those
  }
}

/** Map the bot's platform string to @vexa/join's Platform ('teams' | 'zoom' | 'jitsi' | 'google_meet'). */
function joinPlatform(p: string): JoinPlatform {
  return (p === 'teams' || p === 'zoom' || p === 'jitsi') ? p : 'google_meet';
}

export function createBrowserJoinDriver(page: Page, inv: Invocation): JoinDriver {
  const platform = joinPlatform(inv.platform);
  return {
    async join(report): Promise<JoinResult> {
      let r;
      try {
        r = await joinMeeting(page, {
          meetingUrl: inv.meetingUrl ?? '',
          platform,
          botName: inv.botName,
          passcode: inv.passcode,                      // zoom passcode screen / jitsi room password
          authenticated: inv.authenticated,            // join as a signed-in user (persistent context)
          waitingRoomTimeoutMs: inv.automaticLeave?.waitingRoomTimeout,
          hooks: { onState: (s: JoinState) => { const bs = mapState(s); if (bs) void report(bs); } },
        });
      } catch (e) {
        // A TYPED admission verdict (denial/lobby_timeout/join_failure) → map its outcome so the
        // control plane records the truth, not a generic retried `join_failure` (G1). CARRY the
        // AdmissionError's own message as the reason text (#926) — that's the real Zoom cause
        // ("auth_required: …", "host did not start …") the terminal lifecycle row would otherwise
        // lose, leaving meeting-api to synthesize "reason: None". A genuinely unexpected throw
        // (browser crash, navigation error) is NOT an AdmissionError → re-raise so the orchestrator
        // classifies it as a transient join_failure (and stamps `reason: String(e)` itself).
        if (e instanceof AdmissionError) return { outcome: admissionOutcomeToJoinOutcome(e.outcome), reason: e.message };
        throw e;
      }
      if (r.admitted) { await report('active'); return { outcome: 'admitted' }; }
      const outcome: JoinOutcome = (r.state === 'blocked' || r.state === 'needs_human_help') ? 'blocked' : 'rejected';
      return { outcome, reason: `join ended in state '${r.state}' without admission` };
    },
    onRemoval(cb) {
      if (platform === 'teams') return startTeamsRemovalMonitor(page, cb);
      if (platform === 'zoom')  return startZoomRemovalMonitor(page, cb);
      if (platform === 'jitsi') return startJitsiRemovalMonitor(page, cb);
      return startGoogleRemovalMonitor(page, cb);
    },
    async leave(reason) {
      if (platform === 'teams') { await leaveMicrosoftTeams(page, undefined, reason); return; }
      if (platform === 'zoom')  { await leaveZoomMeeting(page, undefined, reason); return; }
      if (platform === 'jitsi') { await leaveJitsiMeeting(page, undefined, reason); return; }
      await leaveGoogleMeet(page, undefined, reason);
    },
    async withdraw(reason) {
      // Bug 2 — cancel a PENDING join from the waiting room / pre-join screen. Two-step, both
      // best-effort:
      //  1. Click the platform's cancel/leave affordance. The stateless leave-click helpers already
      //     include the waiting-room selectors: Teams' teamsLeaveButtonMatchers carry the "Cancel" buttons
      //     "(for awaiting admission/waiting room)", and googleLeaveButtonMatchers include Cancel/Close. On
      //     Zoom the web client shows no reliable pre-admit cancel, so step 2 is the withdraw there.
      //  2. GUARANTEED DROP: close the page. Google Meet's lobby often exposes no clickable Cancel
      //     (just "Asking to join…"), so closing the tab is the reliable way to abandon the request;
      //     it is also the universal fallback if the cancel click missed. Closing the page after the
      //     click is harmless (the click already fired).
      try {
        if (platform === 'teams')      await leaveMicrosoftTeams(page, undefined, reason);
        else if (platform === 'zoom')  await leaveZoomMeeting(page, undefined, reason);
        else if (platform === 'jitsi') await leaveJitsiMeeting(page, undefined, reason);
        else                           await leaveGoogleMeet(page, undefined, reason);
      } catch { /* best-effort: fall through to the guaranteed page close */ }
      try {
        if (!page.isClosed()) await page.close({ runBeforeUnload: false });
      } catch { /* best-effort */ }
    },
  };
}

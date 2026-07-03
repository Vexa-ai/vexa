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
  leaveGoogleMeet, leaveMicrosoftTeams, leaveZoomMeeting,
  startGoogleRemovalMonitor, startTeamsRemovalMonitor, startZoomRemovalMonitor,
  type JoinState, type Platform as JoinPlatform, type AdmissionOutcome,
} from '@vexa/join';
import type { BotStatus } from './contracts.js';
import type { Invocation } from './config.js';
import type { JoinDriver, JoinOutcome } from './ports.js';

/**
 * Map @vexa/join's typed AdmissionError `outcome` → a JoinOutcome (G1).
 *
 * The admission wait THROWS an `AdmissionError` carrying a precise `outcome`; without this the
 * orchestrator's catch blanket-maps every throw to a transient `join_failure` → the retry classifier
 * (`lifecycle/retry.py`) RE-SPAWNS a bot that was actually DENIED, burning quota. Mapping the outcome
 * keeps the truth: a `denial` → `rejected` → `awaiting_admission_rejected` (PERMANENT, no retry); a
 * `lobby_timeout` → `timeout` → `awaiting_admission_timeout` (transient, a legit retry); a `join_failure`
 * stays `error` → `join_failure` (transient). NB: a distinct `blocked` reason needs a sealed-contract
 * `CompletionReason` value (lane:contract) — until then a detected block surfaces via this same path.
 */
export function admissionOutcomeToJoinOutcome(outcome: AdmissionOutcome): JoinOutcome {
  switch (outcome) {
    case 'denial':        return 'rejected';
    case 'lobby_timeout': return 'timeout';
    case 'join_failure':  return 'error';
    default:              return 'error';
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

/** Map the bot's platform string to @vexa/join's Platform ('teams' | 'zoom' | 'google_meet'). */
function joinPlatform(p: string): JoinPlatform {
  return (p === 'teams' || p === 'zoom') ? p : 'google_meet';
}

export function createBrowserJoinDriver(page: Page, inv: Invocation): JoinDriver {
  const platform = joinPlatform(inv.platform);
  return {
    async join(report): Promise<JoinOutcome> {
      let r;
      try {
        r = await joinMeeting(page, {
          meetingUrl: inv.meetingUrl ?? '',
          platform,
          botName: inv.botName,
          authenticated: inv.authenticated,            // join as a signed-in user (persistent context)
          waitingRoomTimeoutMs: inv.automaticLeave?.waitingRoomTimeout,
          hooks: { onState: (s: JoinState) => { const bs = mapState(s); if (bs) void report(bs); } },
        });
      } catch (e) {
        // A TYPED admission verdict (denial/lobby_timeout/join_failure) → map its outcome so the
        // control plane records the truth, not a generic retried `join_failure` (G1). A genuinely
        // unexpected throw (browser crash, navigation error) is NOT an AdmissionError → re-raise so
        // the orchestrator classifies it as a transient join_failure.
        if (e instanceof AdmissionError) return admissionOutcomeToJoinOutcome(e.outcome);
        throw e;
      }
      if (r.admitted) { await report('active'); return 'admitted'; }
      return (r.state === 'blocked' || r.state === 'needs_human_help') ? 'blocked' : 'rejected';
    },
    onRemoval(cb) {
      if (platform === 'teams') return startTeamsRemovalMonitor(page, cb);
      if (platform === 'zoom')  return startZoomRemovalMonitor(page, cb);
      return startGoogleRemovalMonitor(page, cb);
    },
    async leave(reason) {
      if (platform === 'teams') { await leaveMicrosoftTeams(page, undefined, reason); return; }
      if (platform === 'zoom')  { await leaveZoomMeeting(page, undefined, reason); return; }
      await leaveGoogleMeet(page, undefined, reason);
    },
  };
}

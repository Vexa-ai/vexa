import { log } from "../utils";

export type MeetingStatus =
  | "joining"
  | "awaiting_admission"
  | "active"
  | "needs_human_help"
  | "completed"
  | "failed";

export type CompletionReason =
  | "stopped"
  | "validation_error"
  | "awaiting_admission_timeout"
  | "awaiting_admission_rejected"
  | "left_alone"
  | "evicted"
  | "max_bot_time_exceeded"
  // Pack D (#5): distinct pre-lobby join failure (fast fail before reaching waiting room)
  | "join_failure";

export type FailureStage =
  | "requested"
  | "joining"
  | "awaiting_admission"
  | "active";

// v0.10.6 (#294) — bot-side failure_stage tracker keep-current.
//
// `currentLifecycleStage` is updated on each successful status_change
// callback emission for joining / awaiting_admission / active so that
// crashes past joining no longer get persisted as `failure_stage:
// "joining"`. v0.10.5's #276 added server-side derivation in
// MeetingResponse.from_orm — that fixes the API surface but the JSONB
// row still stored the wrong value at write-time. This tracker fixes
// it at write-time too.
//
// The lifecycle ordering is requested → joining → awaiting_admission →
// active. The tracker only ever advances (never goes backwards), so
// ordering is enforced via STAGE_ORDER below.
const STAGE_ORDER: Record<FailureStage, number> = {
  requested: 0,
  joining: 1,
  awaiting_admission: 2,
  active: 3,
};

let currentLifecycleStage: FailureStage = "joining";

export function getCurrentLifecycleStage(): FailureStage {
  return currentLifecycleStage;
}

/**
 * Advance the lifecycle stage tracker. Only advances forward — calling
 * with an earlier stage is a no-op (defends against stage emit races
 * where a delayed `joining` callback arrives after `active` has emitted).
 */
export function advanceLifecycleStage(next: FailureStage): void {
  if (STAGE_ORDER[next] > STAGE_ORDER[currentLifecycleStage]) {
    currentLifecycleStage = next;
  }
}

export interface UnifiedCallbackPayload {
  connection_id: string;
  container_id?: string;
  status: MeetingStatus;
  reason?: string;
  exit_code?: number;
  error_details?: any;
  platform_specific_error?: string;
  completion_reason?: CompletionReason;
  failure_stage?: FailureStage;
  timestamp?: string;
  speaker_events?: any[];
  // v0.10.5.3 Pack O: ring-buffer of last N structured-JSON log lines from
  // the bot's stdout, sent on terminal status_change for forensic capture.
  // meeting-api persists these into meetings.data.bot_logs JSONB.
  bot_logs?: string[];
  // v0.10.5.3 Pack T: cgroup-based resource summary at exit time.
  // meeting-api persists this into meetings.data.bot_resources JSONB.
  bot_resources?: {
    samples: number;
    peak_memory_bytes: number | null;
    last_memory_bytes: number | null;
    cpu_usage_usec_total: number | null;
    sampler_started_at: string;
    cgroup_available: boolean;
  };
}

/**
 * Unified callback function that replaces all individual callback functions.
 * Sends status changes to the unified callback endpoint.
 */
export async function callStatusChangeCallback(
  botConfig: any,
  status: MeetingStatus,
  reason?: string,
  exitCode?: number,
  errorDetails?: any,
  completionReason?: CompletionReason,
  failureStage?: FailureStage,
  speakerEvents?: any[],
  // v0.10.5.3 Pack O + Pack T — terminal-only forensic fields.
  // bot_logs: last N structured-JSON log lines from bot stdout (ring buffer).
  // bot_resources: cgroup memory + CPU summary at exit time.
  // Optional in callback shape; only populated by performGracefulLeave on
  // terminal status (failed/completed). meeting-api persists both into
  // meetings.data JSONB on the status_change handler.
  botLogs?: string[],
  botResources?: UnifiedCallbackPayload["bot_resources"]
): Promise<void> {log(`🔥 UNIFIED CALLBACK: ${status.toUpperCase()} - reason: ${reason || 'none'}`);
  
  if (!botConfig.meetingApiCallbackUrl) {log("Warning: No callback URL configured. Cannot send status change callback.");
    return;
  }

  if (!botConfig.connectionId) {log("Warning: No connection ID configured. Cannot send status change callback.");
    return;
  }

  // Retry logic with exponential backoff.
  // #407 407-B: joining/awaiting_admission/active are the critical pre-/in-meeting
  // lifecycle callbacks. A TRANSIENT meeting-api blip here (timeout / 5xx / network —
  // e.g. during a meeting-api rollout, which is where these failures clustered) must
  // NOT abort an otherwise-fine join. Give the critical statuses a wider budget, and
  // classify transient vs DELIBERATE (4xx / explicit reject body) so the caller can
  // proceed on transient and only abort on a deliberate server rejection.
  const critical = status === "joining" || status === "awaiting_admission" || status === "active";
  const maxRetries = critical ? 5 : 3;
  const baseDelay = 1000; // 1 second
  const maxDelay = 4000;  // cap backoff so the total budget stays bounded
  let sawDeliberate = false; // true once the server deliberately rejects (4xx / explicit non-accepted body)

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    let timeoutId: NodeJS.Timeout | null = null;
    try {
      // Convert the callback URL to the unified endpoint
      const baseUrl = botConfig.meetingApiCallbackUrl.replace('/exited', '/status_change');
      
      const payload: UnifiedCallbackPayload = {
        connection_id: botConfig.connectionId,
        container_id: botConfig.container_name,
        status: status,
        reason: reason,
        exit_code: exitCode,
        error_details: errorDetails,
        completion_reason: completionReason,
        failure_stage: failureStage,
        timestamp: new Date().toISOString(),
        speaker_events: speakerEvents,
        bot_logs: botLogs,
        bot_resources: botResources,
      };

      log(`Sending unified status change callback to ${baseUrl} (attempt ${attempt + 1}/${maxRetries})`);

      // Add timeout: 5 seconds max
      const controller = new AbortController();
      timeoutId = setTimeout(() => controller.abort(), 5000);

      const response = await fetch(baseUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...((botConfig.internalSecret || process.env.INTERNAL_API_SECRET)
            ? { 'X-Internal-Secret': botConfig.internalSecret || process.env.INTERNAL_API_SECRET || '' }
            : {}),
        },
        body: JSON.stringify(payload),
        signal: controller.signal
      });

      if (timeoutId) clearTimeout(timeoutId);

      if (response.ok) {
        // Read and validate response body
        const responseBody = await response.json();
        // `ignored` is returned when the meeting is already in stopping state
        // (stop_requested=true) and the bot's in-flight status-change is
        // superseded. This is a terminal success for the bot's perspective —
        // the server acknowledged the call and deliberately declined to
        // transition because the user already requested stop. Without this,
        // a user-requested DELETE while the bot's joining callback is
        // in-flight triggers 3 retries + a 'server rejected' exception, and
        // the meeting ends up in `failed` instead of `completed`.
        if (
          responseBody.status === 'processed' ||
          responseBody.status === 'ok' ||
          responseBody.status === 'container_updated' ||
          responseBody.status === 'ignored'
        ) {log(`${status} status change callback sent and processed successfully (status=${responseBody.status})`);
          // v0.10.6 (#294): advance the lifecycle tracker on each
          // successful callback. Crashes after this point will now
          // attribute to the correct stage at write-time.
          if (status === "joining" || status === "awaiting_admission" || status === "active") {
            advanceLifecycleStage(status);
          }
          return; // Success, exit retry loop
        } else {log(`Callback returned unexpected status: ${responseBody.status}, detail: ${responseBody.detail || 'none'}`);
          sawDeliberate = true; // 200 OK with an explicit non-accepted status = deliberate server rejection
          // If not last attempt, retry
          if (attempt < maxRetries - 1) {
            const delay = Math.min(baseDelay * Math.pow(2, attempt), maxDelay);
            log(`Retrying in ${delay}ms...`);
            await new Promise(resolve => setTimeout(resolve, delay));
            continue;
          }
        }
      } else {
        const errorText = await response.text().catch(() => 'Unable to read error response');log(`Callback failed with HTTP ${response.status}: ${errorText}`);
        // 4xx = the server deliberately refused (bad request / invalid transition); 5xx = transient.
        if (response.status >= 400 && response.status < 500) sawDeliberate = true;
        // If not last attempt, retry
        if (attempt < maxRetries - 1) {
          const delay = Math.min(baseDelay * Math.pow(2, attempt), maxDelay);
          log(`Retrying in ${delay}ms...`);
          await new Promise(resolve => setTimeout(resolve, delay));
          continue;
        }
      }
    } catch (error: any) {
      if (timeoutId) clearTimeout(timeoutId);
      const isTimeout = error.name === 'AbortError';log(`Callback attempt ${attempt + 1} failed: ${isTimeout ? 'timeout after 5s' : error.message}`);
      
      // If not last attempt, retry
      if (attempt < maxRetries - 1) {
        const delay = Math.min(baseDelay * Math.pow(2, attempt), maxDelay);
        log(`Retrying in ${delay}ms...`);
        await new Promise(resolve => setTimeout(resolve, delay));
      } else {log(`All ${maxRetries} callback attempts failed for ${status} status change.`);
        const err: any = new Error(`All ${maxRetries} callback attempts failed for ${status} status change`);
        err.transient = !sawDeliberate;   // timeout/network exhaustion = transient unless a deliberate reject was seen
        throw err;
      }
    }
  }
  // If we get here without returning (success), all retries were exhausted via non-exception path
  const err: any = new Error(`${status} callback failed: server rejected after ${maxRetries} attempts`);
  err.transient = !sawDeliberate;   // 5xx-only exhaustion is transient; a 4xx/explicit-reject sets sawDeliberate
  throw err;
}

/**
 * Helper function to map exit reasons to completion reasons and failure stages
 */
export function mapExitReasonToStatus(
  reason: string, 
  exitCode: number
): { status: MeetingStatus; completionReason?: CompletionReason; failureStage?: FailureStage } {
  if (exitCode === 0) {
    // Successful exits (completed)
    switch (reason) {
      case "admission_failed":
      case "admission_timeout":
        return { status: "completed", completionReason: "awaiting_admission_timeout" };
      case "self_initiated_leave":
        return { status: "completed", completionReason: "stopped" };
      case "left_alone":
        return { status: "completed", completionReason: "left_alone" };
      case "evicted":
        return { status: "completed", completionReason: "evicted" };
      case "removed_by_admin":
        return { status: "completed", completionReason: "evicted" };
      case "admission_rejected_by_admin":
        return { status: "completed", completionReason: "awaiting_admission_rejected" };
      default:
        return { status: "completed", completionReason: "stopped" };
    }
  } else {
    // Failed exits.
    //
    // v0.10.6 (#294): the tracker keeps the in-process record of the
    // furthest-advanced lifecycle stage. We use it as a FLOOR for the
    // returned failure_stage so a crash that happened post-admission
    // never gets demoted to `joining` just because the reason string
    // looks early-stage.
    //
    // Hard cases (validation_error, missing_meeting_url) still pin to
    // `requested` because by definition they fire before any callback
    // emission has advanced the tracker.
    const trackedStage = getCurrentLifecycleStage();
    switch (reason) {
      case "missing_meeting_url":
        return { status: "failed", failureStage: "requested" };
      case "validation_error":
        return { status: "failed", failureStage: "requested" };
      case "join_meeting_error":
        // Pack D (#5): bot failed to navigate to the meeting URL (pre-lobby
        // fast fail). Distinct from stopped_before_admission (user-stopped)
        // and awaiting_admission_timeout (waited full lobby timeout).
        return { status: "failed", failureStage: trackedStage, completionReason: "join_failure" };
      case "teams_error":
      case "google_meet_error":
      case "zoom_error":
      case "post_join_setup_error":
        // These can fire at any post-requested stage; trust the tracker.
        // (Pre-#294 these all returned "joining" regardless of when they
        // actually fired, mislabeling every active-stage crash as joining.)
        return { status: "failed", failureStage: trackedStage };
      default:
        return { status: "failed", failureStage: trackedStage };
    }
  }
}

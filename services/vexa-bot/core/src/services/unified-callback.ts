import { log } from "../utils";

export type MeetingStatus = 
  | "joining"
  | "awaiting_admission" 
  | "active"
  | "completed"
  | "failed";

export type CompletionReason = 
  | "stopped"
  | "awaiting_admission_timeout"
  | "left_alone"
  | "evicted"
  | "removed_by_admin"
  | "admission_rejected_by_admin";

export type FailureStage = 
  | "requested"
  | "joining"
  | "active";

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
  failureStage?: FailureStage
): Promise<void> {
  log(`🔥 UNIFIED CALLBACK: ${status.toUpperCase()} - reason: ${reason || 'none'}`);
  
  if (!botConfig.botManagerCallbackUrl) {
    log("Warning: No bot manager callback URL configured. Cannot send status change callback.");
    return;
  }

  if (!botConfig.connectionId) {
    log("Warning: No connection ID configured. Cannot send status change callback.");
    return;
  }

  try {
    // Convert the callback URL to the unified endpoint
    const baseUrl = botConfig.botManagerCallbackUrl.replace('/exited', '/status_change');
    
    const payload: UnifiedCallbackPayload = {
      connection_id: botConfig.connectionId,
      container_id: botConfig.container_name,
      status: status,
      reason: reason,
      exit_code: exitCode,
      error_details: errorDetails,
      completion_reason: completionReason,
      failure_stage: failureStage,
      timestamp: new Date().toISOString()
    };

    log(`Sending unified status change callback to ${baseUrl} with payload: ${JSON.stringify(payload)}`);

    const response = await fetch(baseUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload)
    });

    if (response.ok) {
      log(`${status} status change callback sent successfully`);
    } else {
      log(`${status} status change callback failed with status: ${response.status}`);
    }
  } catch (error: any) {
    log(`Error sending ${status} status change callback: ${error.message}`);
  }
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
        return { status: "completed", completionReason: "awaiting_admission_timeout" };
      case "self_initiated_leave":
        return { status: "completed", completionReason: "stopped" };
      case "left_alone":
        return { status: "completed", completionReason: "left_alone" };
      case "evicted":
        return { status: "completed", completionReason: "evicted" };
      case "removed_by_admin":
        return { status: "completed", completionReason: "removed_by_admin" };
      case "admission_rejected_by_admin":
        return { status: "completed", completionReason: "admission_rejected_by_admin" };
      default:
        return { status: "completed", completionReason: "stopped" };
    }
  } else {
    // Failed exits
    switch (reason) {
      case "teams_error":
      case "google_meet_error":
      case "zoom_error":
        return { status: "failed", failureStage: "joining" };
      case "post_join_setup_error":
        return { status: "failed", failureStage: "joining" };
      case "missing_meeting_url":
        return { status: "failed", failureStage: "requested" };
      case "validation_error":
        return { status: "failed", failureStage: "requested" };
      default:
        return { status: "failed", failureStage: "active" };
    }
  }
}

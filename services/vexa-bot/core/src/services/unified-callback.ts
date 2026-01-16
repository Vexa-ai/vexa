import { log } from "../utils";

export type MeetingStatus = 
  | "joining"
  | "awaiting_admission" 
  | "active"
  | "completed"
  | "failed";

export type CompletionReason = 
  | "stopped"
  | "validation_error"
  | "awaiting_admission_timeout"
  | "awaiting_admission_rejected"
  | "left_alone"
  | "evicted";

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
): Promise<void> {log(`🔥 UNIFIED CALLBACK: ${status.toUpperCase()} - reason: ${reason || 'none'}`);
  
  if (!botConfig.botManagerCallbackUrl) {log("Warning: No bot manager callback URL configured. Cannot send status change callback.");
    return;
  }

  if (!botConfig.connectionId) {log("Warning: No connection ID configured. Cannot send status change callback.");
    return;
  }

  // Retry logic: try up to 3 times with exponential backoff
  const maxRetries = 3;
  const baseDelay = 1000; // 1 second
  
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    let timeoutId: NodeJS.Timeout | null = null;
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

      log(`Sending unified status change callback to ${baseUrl} (attempt ${attempt + 1}/${maxRetries})`);

      // Add timeout: 5 seconds max
      const controller = new AbortController();
      timeoutId = setTimeout(() => controller.abort(), 5000);

      const response = await fetch(baseUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
        signal: controller.signal
      });

      if (timeoutId) clearTimeout(timeoutId);

      if (response.ok) {
        // Read and validate response body
        const responseBody = await response.json();
        if (responseBody.status === 'processed' || responseBody.status === 'ok' || responseBody.status === 'container_updated') {log(`${status} status change callback sent and processed successfully`);
          return; // Success, exit retry loop
        } else {log(`Callback returned unexpected status: ${responseBody.status}, detail: ${responseBody.detail || 'none'}`);
          // If not last attempt, retry
          if (attempt < maxRetries - 1) {
            const delay = baseDelay * Math.pow(2, attempt);
            log(`Retrying in ${delay}ms...`);
            await new Promise(resolve => setTimeout(resolve, delay));
            continue;
          }
        }
      } else {
        const errorText = await response.text().catch(() => 'Unable to read error response');log(`Callback failed with HTTP ${response.status}: ${errorText}`);
        // If not last attempt, retry
        if (attempt < maxRetries - 1) {
          const delay = baseDelay * Math.pow(2, attempt);
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
        const delay = baseDelay * Math.pow(2, attempt);
        log(`Retrying in ${delay}ms...`);
        await new Promise(resolve => setTimeout(resolve, delay));
      } else {log(`All ${maxRetries} callback attempts failed. Bot-manager may not have received the status change.`);
      }
    }
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
        return { status: "completed", completionReason: "evicted" };
      case "admission_rejected_by_admin":
        return { status: "completed", completionReason: "awaiting_admission_rejected" };
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

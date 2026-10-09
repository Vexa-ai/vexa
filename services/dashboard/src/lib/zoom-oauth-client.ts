import type { CreateBotRequest } from "@/types/vexa";
import { withBasePath } from "@/lib/base-path";

// Earlier builds kept the pending request in sessionStorage; it is now carried
// server-side (encrypted cookie) and only this legacy key is cleared here.
const LEGACY_PENDING_ZOOM_BOT_REQUEST_KEY = "vexa.pending_zoom_bot_request";

type ZoomOAuthStartResponse = {
  authUrl: string;
};

type ZoomOAuthStartPayload = {
  userEmail: string;
  returnTo?: string;
  pendingRequest: CreateBotRequest;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function extractErrorCodeFromDetails(details: unknown): string | null {
  if (!isRecord(details)) return null;

  const detailField = details.detail;
  if (typeof detailField === "string") return null;
  if (isRecord(detailField) && typeof detailField.code === "string") {
    return detailField.code;
  }
  if (typeof details.code === "string") {
    return details.code;
  }
  return null;
}

export function shouldTriggerZoomOAuth(error: unknown, platform: string): boolean {
  if (platform !== "zoom") return false;

  const message = error instanceof Error ? error.message.toLowerCase() : "";
  const details = isRecord(error) ? (error as Record<string, unknown>).details : null;
  const code = extractErrorCodeFromDetails(details);

  if (code && ["ZOOM_AUTH_NOT_CONNECTED", "ZOOM_TOKEN_REFRESH_FAILED"].includes(code)) {
    return true;
  }

  if (message.includes("zoom oauth connection is missing")) return true;
  if (message.includes("provide zoom_obf_token or connect zoom oauth")) return true;
  return false;
}

/** Remove any pending request an earlier build left in sessionStorage. */
export function clearLegacyPendingZoomBotRequest(): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.removeItem(LEGACY_PENDING_ZOOM_BOT_REQUEST_KEY);
  } catch {
    // storage unavailable — nothing to clear
  }
}

/** The pending request returned by /api/zoom/oauth/complete, if any. */
export function pendingZoomBotRequestFrom(completeData: unknown): CreateBotRequest | null {
  if (!isRecord(completeData)) return null;
  const pending = completeData.pendingRequest;
  if (!isRecord(pending)) return null;
  if (typeof pending.platform !== "string" || typeof pending.native_meeting_id !== "string") {
    return null;
  }
  return pending as unknown as CreateBotRequest;
}

export async function startZoomOAuth({
  userEmail,
  returnTo,
  pendingRequest,
}: {
  userEmail: string;
  returnTo?: string;
  pendingRequest: CreateBotRequest;
}): Promise<void> {
  clearLegacyPendingZoomBotRequest();

  const payload: ZoomOAuthStartPayload = {
    userEmail,
    returnTo,
    pendingRequest,
  };

  const resp = await fetch(withBasePath("/api/zoom/oauth/start"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || "Failed to start Zoom OAuth");
  }

  const data = (await resp.json()) as ZoomOAuthStartResponse;
  if (!data?.authUrl) {
    throw new Error("Zoom OAuth URL was not returned");
  }

  window.location.assign(data.authUrl);
}

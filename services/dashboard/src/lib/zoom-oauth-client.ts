import type { CreateBotRequest } from "@/types/vexa";

const PENDING_ZOOM_BOT_REQUEST_KEY = "vexa.pending_zoom_bot_request";

type ZoomOAuthStartResponse = {
  authUrl: string;
};

type ZoomOAuthStartPayload = {
  userEmail: string;
  returnTo?: string;
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

export function savePendingZoomBotRequest(request: CreateBotRequest): void {
  if (typeof window === "undefined") return;
  sessionStorage.setItem(PENDING_ZOOM_BOT_REQUEST_KEY, JSON.stringify(request));
}

export function consumePendingZoomBotRequest(): CreateBotRequest | null {
  if (typeof window === "undefined") return null;

  const raw = sessionStorage.getItem(PENDING_ZOOM_BOT_REQUEST_KEY);
  if (!raw) return null;
  sessionStorage.removeItem(PENDING_ZOOM_BOT_REQUEST_KEY);

  try {
    return JSON.parse(raw) as CreateBotRequest;
  } catch {
    return null;
  }
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
  savePendingZoomBotRequest(pendingRequest);

  const payload: ZoomOAuthStartPayload = {
    userEmail,
    returnTo,
  };

  const resp = await fetch("/api/zoom/oauth/start", {
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

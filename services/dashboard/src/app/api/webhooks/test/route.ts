import { NextRequest, NextResponse } from "next/server";
import crypto from "crypto";
import { getAuthenticatedUserId } from "@/lib/auth-utils";

const getAdminConfig = () => {
  const VEXA_ADMIN_API_URL =
    process.env.VEXA_ADMIN_API_URL ||
    process.env.VEXA_API_URL ||
    "http://localhost:18056";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";
  return { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY };
};

async function logDelivery(
  userId: string,
  delivery: Record<string, unknown>
) {
  const { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY } = getAdminConfig();
  if (!VEXA_ADMIN_API_KEY || !userId) return;

  try {
    // Fetch current user data
    const userRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
      headers: { "X-Admin-API-Key": VEXA_ADMIN_API_KEY },
      cache: "no-store",
    });
    if (!userRes.ok) return;

    const userData = await userRes.json();
    const currentData = userData.data || {};
    const deliveries: Array<Record<string, unknown>> = currentData.webhook_deliveries || [];

    // Prepend new delivery, keep last 100
    deliveries.unshift(delivery);
    if (deliveries.length > 100) deliveries.length = 100;

    // Save back
    await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-API-Key": VEXA_ADMIN_API_KEY,
      },
      body: JSON.stringify({
        data: { ...currentData, webhook_deliveries: deliveries },
      }),
    });
  } catch {}
}

/**
 * POST /api/webhooks/test — send a test webhook to the configured endpoint
 */
export async function POST(request: NextRequest) {
  const { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY } = getAdminConfig();

  // Resolve user from authenticated token instead of client-supplied userId
  const userId = await getAuthenticatedUserId();
  if (!userId) {
    return NextResponse.json({ success: false, error: "Not authenticated" }, { status: 401 });
  }

  try {
    const body = await request.json();
    const url = body.url;

    if (!url) {
      return NextResponse.json({ success: false, error: "No webhook URL provided" }, { status: 400 });
    }

    // Build a test payload
    const testPayload = {
      event: "test",
      timestamp: new Date().toISOString(),
      data: {
        message: "This is a test webhook from Vexa",
        meeting_id: "test-meeting-123",
        platform: "test",
      },
    };

    const payloadStr = JSON.stringify(testPayload);

    // Sign the payload if we have a secret for this user
    let signature = "";
    if (VEXA_ADMIN_API_KEY && userId) {
      try {
        const userRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {
          headers: { "X-Admin-API-Key": VEXA_ADMIN_API_KEY },
          cache: "no-store",
        });
        if (userRes.ok) {
          const userData = await userRes.json();
          const secret = userData.data?.webhook_secret;
          if (secret) {
            signature = crypto
              .createHmac("sha256", secret)
              .update(payloadStr)
              .digest("hex");
          }
        }
      } catch {}
    }

    // Send the test webhook
    const startTime = Date.now();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "User-Agent": "Vexa-Webhook/1.0",
      "X-Vexa-Event": "test",
    };
    if (signature) {
      headers["X-Vexa-Signature"] = `sha256=${signature}`;
    }

    let webhookRes: Response;
    let timeMs: number;
    let errorMsg = "";

    try {
      webhookRes = await fetch(url, {
        method: "POST",
        headers,
        body: payloadStr,
        signal: AbortSignal.timeout(10000),
      });
      timeMs = Date.now() - startTime;
    } catch (fetchError) {
      timeMs = Date.now() - startTime;
      errorMsg = (fetchError as Error).message;
      if (errorMsg.includes("timeout")) errorMsg = "Webhook timed out (10s)";

      // Log failed delivery
      await logDelivery(userId, {
        id: crypto.randomUUID(),
        event: "test",
        meeting_id: "test-meeting-123",
        meeting_name: "Test Webhook",
        status: "failed",
        attempts: 1,
        max_attempts: 1,
        response_status: null,
        response_time_ms: timeMs,
        endpoint_url: url,
        created_at: new Date().toISOString(),
        last_attempt_at: new Date().toISOString(),
      });

      return NextResponse.json({
        success: false,
        error: errorMsg,
      }, { status: 500 });
    }

    // Log delivery to user data
    await logDelivery(userId, {
      id: crypto.randomUUID(),
      event: "test",
      meeting_id: "test-meeting-123",
      meeting_name: "Test Webhook",
      status: webhookRes.ok ? "delivered" : "failed",
      attempts: 1,
      max_attempts: 1,
      response_status: webhookRes.status,
      response_time_ms: timeMs,
      endpoint_url: url,
      created_at: new Date().toISOString(),
      last_attempt_at: new Date().toISOString(),
    });

    return NextResponse.json({
      success: webhookRes.ok,
      status: webhookRes.status,
      time_ms: timeMs,
      ...(!webhookRes.ok && { error: `HTTP ${webhookRes.status}` }),
    });
  } catch (error) {
    const msg = (error as Error).message;
    return NextResponse.json({
      success: false,
      error: msg.includes("timeout") ? "Webhook timed out (10s)" : msg,
    }, { status: 500 });
  }
}

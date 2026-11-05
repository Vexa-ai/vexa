import { NextRequest, NextResponse } from "next/server";
import {
  DEFAULT_WEBHOOK_EVENTS,
  UserWebhookError,
  generateWebhookSecret,
  getUserWebhook,
  maskWebhookSecret,
  putUserWebhook,
  type UserWebhookUpdate,
} from "@/lib/user-webhook";

function errorResponse(error: unknown) {
  if (error instanceof UserWebhookError) {
    return NextResponse.json({ error: error.message }, { status: error.status });
  }
  return NextResponse.json({ error: (error as Error).message }, { status: 500 });
}

/**
 * GET /api/webhooks/config — the signed-in user's webhook configuration
 */
export async function GET() {
  try {
    const webhook = await getUserWebhook();
    return NextResponse.json({
      endpoint_url: webhook.webhook_url || "",
      signing_secret_masked: webhook.webhook_secret_set ? webhook.webhook_secret : null,
      events: webhook.webhook_events || DEFAULT_WEBHOOK_EVENTS,
    });
  } catch (error) {
    return errorResponse(error);
  }
}

/**
 * PUT /api/webhooks/config — save the signed-in user's webhook configuration
 *
 * A secret the user typed is stored as given. Without one, the stored secret
 * is kept, and an endpoint saved with no secret at all gets a generated one.
 */
export async function PUT(request: NextRequest) {
  try {
    const body = await request.json();
    const endpointUrl: string = typeof body.endpoint_url === "string" ? body.endpoint_url : "";
    const signingSecret: string =
      typeof body.signing_secret === "string" ? body.signing_secret.trim() : "";

    const current = await getUserWebhook();
    const events: Record<string, boolean> =
      body.events || current.webhook_events || DEFAULT_WEBHOOK_EVENTS;

    const update: UserWebhookUpdate = { webhook_url: endpointUrl, webhook_events: events };
    let maskedSecret = current.webhook_secret_set ? current.webhook_secret : null;
    const newSecret = signingSecret || (!current.webhook_secret_set && endpointUrl ? generateWebhookSecret() : "");
    if (newSecret) {
      update.webhook_secret = newSecret;
      maskedSecret = maskWebhookSecret(newSecret);
    }

    await putUserWebhook(update);

    return NextResponse.json({
      endpoint_url: endpointUrl,
      signing_secret_masked: maskedSecret,
      events,
    });
  } catch (error) {
    return errorResponse(error);
  }
}

import { NextResponse } from "next/server";
import {
  UserWebhookError,
  generateWebhookSecret,
  getUserWebhook,
  putUserWebhook,
} from "@/lib/user-webhook";

/**
 * POST /api/webhooks/rotate-secret — rotate the signed-in user's webhook signing secret
 *
 * The new secret is returned once, here; reads afterwards show it masked.
 */
export async function POST() {
  try {
    const current = await getUserWebhook();
    const newSecret = generateWebhookSecret();
    await putUserWebhook({
      webhook_url: current.webhook_url || "",
      webhook_secret: newSecret,
      ...(current.webhook_events ? { webhook_events: current.webhook_events } : {}),
    });
    return NextResponse.json({ signing_secret: newSecret });
  } catch (error) {
    if (error instanceof UserWebhookError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: (error as Error).message }, { status: 500 });
  }
}

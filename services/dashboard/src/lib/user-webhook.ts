import crypto from "crypto";
import { cookies } from "next/headers";
import { getAuthCookieName } from "@/lib/auth-cookies";

/**
 * The signed-in user's webhook configuration, read and written through the
 * gateway's self-serve `/user/webhook` tier with the user's own API token.
 *
 * Reads never return the secret in the clear: the gateway masks it and says
 * whether one is set.
 */
export type UserWebhook = {
  webhook_url: string | null;
  webhook_secret_set: boolean;
  webhook_secret: string | null;
  webhook_events: Record<string, boolean> | null;
};

export type UserWebhookUpdate = {
  webhook_url: string;
  webhook_secret?: string;
  webhook_events?: Record<string, boolean>;
};

export const DEFAULT_WEBHOOK_EVENTS: Record<string, boolean> = {
  "meeting.completed": true,
  "meeting.started": false,
  "bot.failed": false,
  "meeting.status_change": false,
};

export class UserWebhookError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export function generateWebhookSecret(): string {
  return `whsec_${crypto.randomBytes(24).toString("base64url")}`;
}

export function maskWebhookSecret(secret: string): string {
  return "****" + secret.slice(-4);
}

async function gatewayRequest(method: "GET" | "PUT", body?: UserWebhookUpdate): Promise<Response> {
  const VEXA_API_URL = process.env.VEXA_API_URL;
  if (!VEXA_API_URL) throw new UserWebhookError("VEXA_API_URL is required", 503);

  const cookieStore = await cookies();
  const token = cookieStore.get(getAuthCookieName())?.value;
  if (!token) throw new UserWebhookError("Not authenticated", 401);

  const headers: Record<string, string> = { "X-API-Key": token };
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(`${VEXA_API_URL}/user/webhook`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (res.status === 401 || res.status === 403) {
    throw new UserWebhookError("Not authenticated", 401);
  }
  if (!res.ok) {
    throw new UserWebhookError(`Gateway returned ${res.status} for ${method} /user/webhook`, 502);
  }
  return res;
}

export async function getUserWebhook(): Promise<UserWebhook> {
  const res = await gatewayRequest("GET");
  const data = (await res.json()) as Partial<UserWebhook>;
  return {
    webhook_url: data.webhook_url ?? null,
    webhook_secret_set: data.webhook_secret_set === true,
    webhook_secret: data.webhook_secret ?? null,
    webhook_events: data.webhook_events ?? null,
  };
}

export async function putUserWebhook(update: UserWebhookUpdate): Promise<void> {
  await gatewayRequest("PUT", update);
}

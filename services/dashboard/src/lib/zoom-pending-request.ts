/**
 * Server-side carriage of the bot request that triggered a Zoom OAuth round
 * trip, so it can be started as soon as the user returns from Zoom.
 *
 * The request may carry a meeting passcode, so it is never kept in browser
 * storage. The OAuth start route encrypts it (AES-256-GCM, key derived from the
 * OAuth state secret) into a short-lived httpOnly cookie bound to the user; the
 * OAuth complete route decrypts it, checks it belongs to the same user, clears
 * the cookie and hands the request back in its response.
 */
import { createCipheriv, createDecipheriv, hkdfSync, randomBytes } from "crypto";
import type { CreateBotRequest, Platform } from "@/types/vexa";

export const ZOOM_PENDING_REQUEST_COOKIE = "vexa_zoom_pending_bot";
export const ZOOM_PENDING_REQUEST_TTL_SECONDS = 10 * 60;

const KEY_INFO = "vexa-dashboard/zoom-pending-bot-request/v1";
const IV_BYTES = 12;
const TAG_BYTES = 16;
const MAX_FIELD_LENGTH = 2048;

type SealedEnvelope = {
  u: string; // user id the request belongs to
  exp: number; // unix seconds
  r: CreateBotRequest;
};

function deriveKey(secret: string): Buffer {
  return Buffer.from(hkdfSync("sha256", secret, Buffer.alloc(0), KEY_INFO, 32));
}

function boundedString(value: unknown): string | undefined {
  return typeof value === "string" && value.length <= MAX_FIELD_LENGTH ? value : undefined;
}

/** Keep only the known CreateBotRequest fields, with their expected types. */
export function normalizePendingZoomBotRequest(value: unknown): CreateBotRequest | null {
  if (typeof value !== "object" || value === null) return null;
  const input = value as Record<string, unknown>;

  const platform = boundedString(input.platform);
  const nativeMeetingId = boundedString(input.native_meeting_id);
  if (!platform || !nativeMeetingId) return null;

  const request: CreateBotRequest = {
    platform: platform as Platform,
    native_meeting_id: nativeMeetingId,
  };
  const passcode = boundedString(input.passcode);
  if (passcode !== undefined) request.passcode = passcode;
  const meetingUrl = boundedString(input.meeting_url);
  if (meetingUrl !== undefined) request.meeting_url = meetingUrl;
  const botName = boundedString(input.bot_name);
  if (botName !== undefined) request.bot_name = botName;
  const language = boundedString(input.language);
  if (language !== undefined) request.language = language;
  if (typeof input.transcribe_enabled === "boolean") {
    request.transcribe_enabled = input.transcribe_enabled;
  }
  if (typeof input.authenticated === "boolean") request.authenticated = input.authenticated;
  return request;
}

export function encryptPendingZoomBotRequest(
  request: CreateBotRequest,
  userId: string,
  secret: string,
  nowSeconds: number = Math.floor(Date.now() / 1000)
): string {
  const envelope: SealedEnvelope = {
    u: userId,
    exp: nowSeconds + ZOOM_PENDING_REQUEST_TTL_SECONDS,
    r: request,
  };
  const iv = randomBytes(IV_BYTES);
  const cipher = createCipheriv("aes-256-gcm", deriveKey(secret), iv);
  const ciphertext = Buffer.concat([
    cipher.update(JSON.stringify(envelope), "utf8"),
    cipher.final(),
  ]);
  return Buffer.concat([iv, cipher.getAuthTag(), ciphertext]).toString("base64url");
}

/** Returns the request, or null if the value is missing, tampered, expired or another user's. */
export function decryptPendingZoomBotRequest(
  sealed: string | undefined,
  userId: string,
  secret: string,
  nowSeconds: number = Math.floor(Date.now() / 1000)
): CreateBotRequest | null {
  if (!sealed) return null;
  try {
    const raw = Buffer.from(sealed, "base64url");
    if (raw.length <= IV_BYTES + TAG_BYTES) return null;
    const iv = raw.subarray(0, IV_BYTES);
    const tag = raw.subarray(IV_BYTES, IV_BYTES + TAG_BYTES);
    const ciphertext = raw.subarray(IV_BYTES + TAG_BYTES);
    const decipher = createDecipheriv("aes-256-gcm", deriveKey(secret), iv);
    decipher.setAuthTag(tag);
    const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString(
      "utf8"
    );
    const envelope = JSON.parse(plaintext) as Partial<SealedEnvelope>;
    if (envelope.u !== userId) return null;
    if (typeof envelope.exp !== "number" || envelope.exp < nowSeconds) return null;
    return normalizePendingZoomBotRequest(envelope.r);
  } catch {
    return null;
  }
}

export function pendingRequestCookieOptions(secure: boolean, maxAge: number) {
  return {
    httpOnly: true,
    secure,
    sameSite: "lax" as const,
    path: "/",
    maxAge,
  };
}

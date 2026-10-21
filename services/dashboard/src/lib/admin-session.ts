/**
 * Admin console session cookie: `<base64 JSON payload>.<hex HMAC-SHA256>`,
 * signed with JWT_SECRET. Issued by /api/auth/admin-verify; every reader
 * checks the signature and age before trusting it.
 */
import crypto from "crypto";
import { getJwtSecret } from "@/lib/jwt-secret";

export const ADMIN_COOKIE_NAME = "vexa-admin-session";
export const ADMIN_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24; // 24 hours

function hmacHex(payload: string, secret: string): string {
  return crypto.createHmac("sha256", secret).update(payload).digest("hex");
}

/** Returns null when JWT_SECRET is not configured (admin sign-in is then refused). */
export function createAdminSessionValue(nowMs: number = Date.now()): string | null {
  const secret = getJwtSecret();
  if (!secret) return null;
  const payload = Buffer.from(
    JSON.stringify({ authenticated: true, timestamp: nowMs })
  ).toString("base64");
  return `${payload}.${hmacHex(payload, secret)}`;
}

export type AdminSessionCheck = { valid: true } | { valid: false; reason: "missing" | "invalid" | "expired" };

export function checkAdminSessionValue(
  value: string | undefined,
  nowMs: number = Date.now()
): AdminSessionCheck {
  if (!value) return { valid: false, reason: "missing" };
  const secret = getJwtSecret();
  if (!secret) return { valid: false, reason: "invalid" };

  const dotIndex = value.lastIndexOf(".");
  if (dotIndex <= 0) return { valid: false, reason: "invalid" };
  const payload = value.substring(0, dotIndex);
  const given = Buffer.from(value.substring(dotIndex + 1), "utf8");
  const expected = Buffer.from(hmacHex(payload, secret), "utf8");
  if (given.length !== expected.length || !crypto.timingSafeEqual(given, expected)) {
    return { valid: false, reason: "invalid" };
  }

  try {
    const data = JSON.parse(Buffer.from(payload, "base64").toString()) as {
      authenticated?: unknown;
      timestamp?: unknown;
    };
    if (data.authenticated !== true || typeof data.timestamp !== "number") {
      return { valid: false, reason: "invalid" };
    }
    if (nowMs - data.timestamp > ADMIN_SESSION_MAX_AGE_SECONDS * 1000) {
      return { valid: false, reason: "expired" };
    }
    return { valid: true };
  } catch {
    return { valid: false, reason: "invalid" };
  }
}

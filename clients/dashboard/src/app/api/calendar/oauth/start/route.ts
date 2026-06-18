import { NextRequest, NextResponse } from "next/server";
import { createHmac } from "crypto";
import { findUserByEmail } from "@/lib/vexa-admin-api";

type CalendarOAuthStatePayload = {
  userId: string;
  email: string;
  returnTo: string;
  redirectUri: string;
  iat: number;
  exp: number;
};

function getGoogleClientId(): string {
  return process.env.GOOGLE_CLIENT_ID || "";
}

function getStateSecret(): string {
  return (
    process.env.GOOGLE_OAUTH_STATE_SECRET ||
    process.env.NEXTAUTH_SECRET ||
    process.env.VEXA_ADMIN_API_KEY ||
    ""
  );
}

function toBase64Url(value: string): string {
  return Buffer.from(value, "utf8")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function signStatePayload(payload: CalendarOAuthStatePayload, secret: string): string {
  const data = toBase64Url(JSON.stringify(payload));
  const signature = createHmac("sha256", secret).update(data).digest("base64url");
  return `${data}.${signature}`;
}

function resolveRedirectUri(req: NextRequest): string {
  if (process.env.GOOGLE_CALENDAR_REDIRECT_URI) {
    return process.env.GOOGLE_CALENDAR_REDIRECT_URI;
  }
  return `${req.nextUrl.origin}/auth/google-calendar/callback`;
}

export async function POST(req: NextRequest) {
  try {
    const { userEmail, returnTo } = (await req.json()) as {
      userEmail?: string;
      returnTo?: string;
    };

    if (!userEmail || typeof userEmail !== "string") {
      return NextResponse.json({ error: "userEmail is required" }, { status: 400 });
    }

    const clientId = getGoogleClientId();
    const secret = getStateSecret();
    if (!clientId || !secret) {
      return NextResponse.json(
        { error: "Google Calendar OAuth is not configured" },
        { status: 500 }
      );
    }

    const userResult = await findUserByEmail(userEmail);
    if (!userResult.success || !userResult.data) {
      return NextResponse.json(
        { error: userResult.error?.message || "Could not resolve user" },
        { status: 400 }
      );
    }

    const now = Math.floor(Date.now() / 1000);
    const redirectUri = resolveRedirectUri(req);
    const payload: CalendarOAuthStatePayload = {
      userId: String(userResult.data.id),
      email: userResult.data.email,
      returnTo: typeof returnTo === "string" && returnTo.startsWith("/") ? returnTo : "/meetings",
      redirectUri,
      iat: now,
      exp: now + 10 * 60,
    };

    const state = signStatePayload(payload, secret);

    const authUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
    authUrl.searchParams.set("response_type", "code");
    authUrl.searchParams.set("client_id", clientId);
    authUrl.searchParams.set("redirect_uri", redirectUri);
    authUrl.searchParams.set("state", state);
    authUrl.searchParams.set("scope", "https://www.googleapis.com/auth/calendar.readonly");
    authUrl.searchParams.set("access_type", "offline");
    authUrl.searchParams.set("prompt", "consent");

    return NextResponse.json({
      authUrl: authUrl.toString(),
    });
  } catch (error) {
    return NextResponse.json(
      { error: `Failed to initialize Google Calendar OAuth: ${(error as Error).message}` },
      { status: 500 }
    );
  }
}

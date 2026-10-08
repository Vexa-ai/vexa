import { NextRequest, NextResponse } from "next/server";
import { createHmac } from "crypto";
import { findUserByEmail } from "@/lib/vexa-admin-api";

type ZoomOAuthStatePayload = {
  userId: string;
  email: string;
  returnTo: string;
  redirectUri: string;
  iat: number;
  exp: number;
};

function getZoomClientId(): string {
  return process.env.ZOOM_OAUTH_CLIENT_ID || process.env.ZOOM_CLIENT_ID || "";
}

function getStateSecret(): string {
  return (
    process.env.ZOOM_OAUTH_STATE_SECRET ||
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

function signStatePayload(payload: ZoomOAuthStatePayload, secret: string): string {
  const data = toBase64Url(JSON.stringify(payload));
  const signature = createHmac("sha256", secret).update(data).digest("base64url");
  return `${data}.${signature}`;
}

function resolveRedirectUri(req: NextRequest): string {
  if (process.env.ZOOM_OAUTH_REDIRECT_URI) {
    return process.env.ZOOM_OAUTH_REDIRECT_URI;
  }
  return `${req.nextUrl.origin}/auth/zoom/callback`;
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

    const clientId = getZoomClientId();
    const secret = getStateSecret();
    if (!clientId || !secret) {
      return NextResponse.json(
        { error: "Zoom OAuth is not configured on the dashboard" },
        { status: 500 }
      );
    }

    const userResult = await findUserByEmail(userEmail);
    if (!userResult.success || !userResult.data) {
      return NextResponse.json(
        { error: userResult.error?.message || "Could not resolve user for Zoom OAuth" },
        { status: 400 }
      );
    }

    const now = Math.floor(Date.now() / 1000);
    const redirectUri = resolveRedirectUri(req);
    const payload: ZoomOAuthStatePayload = {
      userId: String(userResult.data.id),
      email: userResult.data.email,
      returnTo: typeof returnTo === "string" && returnTo.startsWith("/") ? returnTo : "/meetings",
      redirectUri,
      iat: now,
      exp: now + 10 * 60,
    };

    const state = signStatePayload(payload, secret);

    const authUrl = new URL("https://zoom.us/oauth/authorize");
    authUrl.searchParams.set("response_type", "code");
    authUrl.searchParams.set("client_id", clientId);
    authUrl.searchParams.set("redirect_uri", redirectUri);
    authUrl.searchParams.set("state", state);

    return NextResponse.json({
      authUrl: authUrl.toString(),
    });
  } catch (error) {
    return NextResponse.json(
      { error: `Failed to initialize Zoom OAuth: ${(error as Error).message}` },
      { status: 500 }
    );
  }
}

import { NextRequest, NextResponse } from "next/server";
import { createHmac } from "crypto";
import { getAuthenticatedUser } from "@/lib/auth-utils";
import { getUserById } from "@/lib/vexa-admin-api";

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
  // Unpadded base64url (RFC 4648 §5), produced natively.
  return Buffer.from(value, "utf8").toString("base64url");
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
    // The account being connected is always the signed-in user's. A
    // `userEmail` in the body (sent by older clients) is ignored.
    const { returnTo } = (await req.json()) as {
      returnTo?: string;
    };

    const clientId = getGoogleClientId();
    const secret = getStateSecret();
    if (!clientId || !secret) {
      return NextResponse.json(
        { error: "Google Calendar OAuth is not configured" },
        { status: 500 }
      );
    }

    const sessionUser = await getAuthenticatedUser();
    if (!sessionUser) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }
    let email = sessionUser.email;
    if (!email) {
      const userResult = await getUserById(sessionUser.id);
      email = userResult.success && userResult.data ? userResult.data.email : "";
    }
    if (!email) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }

    const now = Math.floor(Date.now() / 1000);
    const redirectUri = resolveRedirectUri(req);
    const payload: CalendarOAuthStatePayload = {
      userId: sessionUser.id,
      email,
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

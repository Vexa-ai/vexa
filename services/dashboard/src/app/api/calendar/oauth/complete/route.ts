import { NextRequest, NextResponse } from "next/server";
import { createHmac } from "crypto";
import { getUserById, updateUser } from "@/lib/vexa-admin-api";

type CalendarOAuthStatePayload = {
  userId: string;
  email: string;
  returnTo: string;
  redirectUri?: string;
  iat: number;
  exp: number;
};

function getGoogleClientId(): string {
  return process.env.GOOGLE_CLIENT_ID || "";
}

function getGoogleClientSecret(): string {
  return process.env.GOOGLE_CLIENT_SECRET || "";
}

function getStateSecret(): string {
  return (
    process.env.GOOGLE_OAUTH_STATE_SECRET ||
    process.env.NEXTAUTH_SECRET ||
    process.env.VEXA_ADMIN_API_KEY ||
    ""
  );
}

function resolveRedirectUri(req: NextRequest): string {
  if (process.env.GOOGLE_CALENDAR_REDIRECT_URI) {
    return process.env.GOOGLE_CALENDAR_REDIRECT_URI;
  }
  return `${req.nextUrl.origin}/auth/google-calendar/callback`;
}

function parseAndVerifyState(state: string, secret: string): CalendarOAuthStatePayload {
  const [data, signature] = state.split(".");
  if (!data || !signature) {
    throw new Error("Invalid state format");
  }

  const expectedSig = createHmac("sha256", secret).update(data).digest("base64url");
  if (signature !== expectedSig) {
    throw new Error("Invalid state signature");
  }

  const raw = Buffer.from(
    data.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((data.length + 3) % 4),
    "base64"
  ).toString("utf8");

  const payload = JSON.parse(raw) as CalendarOAuthStatePayload;
  const now = Math.floor(Date.now() / 1000);
  if (!payload.exp || payload.exp < now) {
    throw new Error("OAuth state expired");
  }
  if (!payload.userId || !payload.email) {
    throw new Error("OAuth state is missing user data");
  }
  return payload;
}

async function exchangeCodeForGoogleTokens({
  code,
  redirectUri,
  clientId,
  clientSecret,
}: {
  code: string;
  redirectUri: string;
  clientId: string;
  clientSecret: string;
}): Promise<{
  access_token: string;
  refresh_token: string;
  expires_in: number;
  scope?: string;
}> {
  const params = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri,
    client_id: clientId,
    client_secret: clientSecret,
  });

  const resp = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: params.toString(),
    cache: "no-store",
  });

  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(`Google token exchange failed (${resp.status}): ${text}`);
  }

  const payload = JSON.parse(text) as {
    access_token?: string;
    refresh_token?: string;
    expires_in?: number;
    scope?: string;
  };

  if (!payload.access_token || !payload.refresh_token) {
    throw new Error("Google token response missing access_token or refresh_token");
  }

  return {
    access_token: payload.access_token,
    refresh_token: payload.refresh_token,
    expires_in: Number(payload.expires_in || 3600),
    scope: payload.scope,
  };
}

export async function POST(req: NextRequest) {
  try {
    const { code, state } = (await req.json()) as {
      code?: string;
      state?: string;
    };

    if (!code || !state) {
      return NextResponse.json({ error: "code and state are required" }, { status: 400 });
    }

    const clientId = getGoogleClientId();
    const clientSecret = getGoogleClientSecret();
    const stateSecret = getStateSecret();
    if (!clientId || !clientSecret || !stateSecret) {
      return NextResponse.json(
        { error: "Google Calendar OAuth is not configured" },
        { status: 500 }
      );
    }

    const parsedState = parseAndVerifyState(state, stateSecret);
    const redirectUri =
      typeof parsedState.redirectUri === "string" && parsedState.redirectUri
        ? parsedState.redirectUri
        : resolveRedirectUri(req);

    const tokens = await exchangeCodeForGoogleTokens({
      code,
      redirectUri,
      clientId,
      clientSecret,
    });

    const userResult = await getUserById(parsedState.userId);
    if (!userResult.success || !userResult.data) {
      return NextResponse.json(
        { error: userResult.error?.message || "Failed to load user" },
        { status: 500 }
      );
    }

    const now = Math.floor(Date.now() / 1000);
    const existingData =
      userResult.data.data && typeof userResult.data.data === "object"
        ? userResult.data.data
        : {};

    const updatedData: Record<string, unknown> = {
      ...existingData,
      google_calendar: {
        oauth: {
          access_token: tokens.access_token,
          refresh_token: tokens.refresh_token,
          expires_at: now + tokens.expires_in,
          scope: tokens.scope || "",
        },
      },
    };

    const patchResult = await updateUser(parsedState.userId, { data: updatedData });
    if (!patchResult.success) {
      return NextResponse.json(
        { error: patchResult.error?.message || "Failed to persist Google Calendar tokens" },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      returnTo: parsedState.returnTo || "/meetings",
    });
  } catch (error) {
    return NextResponse.json(
      { error: `Failed to complete Google Calendar OAuth: ${(error as Error).message}` },
      { status: 500 }
    );
  }
}

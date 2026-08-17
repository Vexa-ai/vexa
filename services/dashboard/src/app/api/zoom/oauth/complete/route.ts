import { NextRequest, NextResponse } from "next/server";
import { createHmac } from "crypto";
import { getUserById, updateUser } from "@/lib/vexa-admin-api";

type ZoomOAuthStatePayload = {
  userId: string;
  email: string;
  returnTo: string;
  redirectUri?: string;
  iat: number;
  exp: number;
};

function getZoomClientId(): string {
  return process.env.ZOOM_OAUTH_CLIENT_ID || process.env.ZOOM_CLIENT_ID || "";
}

function getZoomClientSecret(): string {
  return process.env.ZOOM_OAUTH_CLIENT_SECRET || process.env.ZOOM_CLIENT_SECRET || "";
}

function getStateSecret(): string {
  return (
    process.env.ZOOM_OAUTH_STATE_SECRET ||
    process.env.NEXTAUTH_SECRET ||
    process.env.VEXA_ADMIN_API_KEY ||
    ""
  );
}

function resolveRedirectUri(req: NextRequest): string {
  if (process.env.ZOOM_OAUTH_REDIRECT_URI) {
    return process.env.ZOOM_OAUTH_REDIRECT_URI;
  }
  return `${req.nextUrl.origin}/auth/zoom/callback`;
}

function parseAndVerifyState(state: string, secret: string): ZoomOAuthStatePayload {
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

  const payload = JSON.parse(raw) as ZoomOAuthStatePayload;
  const now = Math.floor(Date.now() / 1000);
  if (!payload.exp || payload.exp < now) {
    throw new Error("OAuth state expired");
  }
  if (!payload.userId || !payload.email) {
    throw new Error("OAuth state is missing user data");
  }
  return payload;
}

async function exchangeCodeForZoomTokens({
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
  const basic = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");
  const params = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri,
  });

  const resp = await fetch(`https://zoom.us/oauth/token?${params.toString()}`, {
    method: "POST",
    headers: {
      Authorization: `Basic ${basic}`,
    },
    cache: "no-store",
  });

  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(`Zoom token exchange failed (${resp.status}): ${text}`);
  }

  const payload = JSON.parse(text) as {
    access_token?: string;
    refresh_token?: string;
    expires_in?: number;
    scope?: string;
  };

  if (!payload.access_token || !payload.refresh_token) {
    throw new Error("Zoom token response missing access_token or refresh_token");
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

    const clientId = getZoomClientId();
    const clientSecret = getZoomClientSecret();
    const stateSecret = getStateSecret();
    if (!clientId || !clientSecret || !stateSecret) {
      return NextResponse.json(
        { error: "Zoom OAuth is not configured on the dashboard" },
        { status: 500 }
      );
    }

    const parsedState = parseAndVerifyState(state, stateSecret);
    const redirectUri =
      typeof parsedState.redirectUri === "string" && parsedState.redirectUri
        ? parsedState.redirectUri
        : resolveRedirectUri(req);

    const tokens = await exchangeCodeForZoomTokens({
      code,
      redirectUri,
      clientId,
      clientSecret,
    });

    const userResult = await getUserById(parsedState.userId);
    if (!userResult.success || !userResult.data) {
      return NextResponse.json(
        { error: userResult.error?.message || "Failed to load user before Zoom token save" },
        { status: 500 }
      );
    }

    const now = Math.floor(Date.now() / 1000);
    const existingData =
      userResult.data.data && typeof userResult.data.data === "object"
        ? userResult.data.data
        : {};

    const existingZoom =
      (existingData as Record<string, unknown>).zoom &&
      typeof (existingData as Record<string, unknown>).zoom === "object"
        ? ((existingData as Record<string, unknown>).zoom as Record<string, unknown>)
        : {};

    const updatedData: Record<string, unknown> = {
      ...existingData,
      zoom: {
        ...existingZoom,
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
        { error: patchResult.error?.message || "Failed to persist Zoom OAuth tokens" },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      returnTo: parsedState.returnTo || "/meetings",
    });
  } catch (error) {
    return NextResponse.json(
      { error: `Failed to complete Zoom OAuth: ${(error as Error).message}` },
      { status: 500 }
    );
  }
}

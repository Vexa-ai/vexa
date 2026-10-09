import { NextRequest, NextResponse } from "next/server";
import { createHmac } from "crypto";
import { getAuthenticatedUser } from "@/lib/auth-utils";
import { getUserById } from "@/lib/vexa-admin-api";
import {
  ZOOM_PENDING_REQUEST_COOKIE,
  ZOOM_PENDING_REQUEST_TTL_SECONDS,
  encryptPendingZoomBotRequest,
  normalizePendingZoomBotRequest,
  pendingRequestCookieOptions,
} from "@/lib/zoom-pending-request";

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
  // Unpadded base64url (RFC 4648 §5), produced natively.
  return Buffer.from(value, "utf8").toString("base64url");
}

function signStatePayload(payload: ZoomOAuthStatePayload, secret: string): string {
  const data = toBase64Url(JSON.stringify(payload));
  const signature = createHmac("sha256", secret).update(data).digest("base64url");
  return `${data}.${signature}`;
}

function isHttpsRequest(req: NextRequest): boolean {
  const forwarded = req.headers.get("x-forwarded-proto")?.split(",")[0]?.trim();
  return forwarded ? forwarded === "https" : req.nextUrl.protocol === "https:";
}

function resolveRedirectUri(req: NextRequest): string {
  if (process.env.ZOOM_OAUTH_REDIRECT_URI) {
    return process.env.ZOOM_OAUTH_REDIRECT_URI;
  }
  return `${req.nextUrl.origin}/auth/zoom/callback`;
}

export async function POST(req: NextRequest) {
  try {
    // The account being connected is always the signed-in user's. A
    // `userEmail` in the body (sent by older clients) is ignored.
    const { returnTo, pendingRequest } = (await req.json()) as {
      returnTo?: string;
      pendingRequest?: unknown;
    };

    const clientId = getZoomClientId();
    const secret = getStateSecret();
    if (!clientId || !secret) {
      return NextResponse.json(
        { error: "Zoom OAuth is not configured on the dashboard" },
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
    const payload: ZoomOAuthStatePayload = {
      userId: sessionUser.id,
      email,
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

    const response = NextResponse.json({
      authUrl: authUrl.toString(),
    });

    // Carry the bot request across the Zoom round trip encrypted, server-side.
    const pending = normalizePendingZoomBotRequest(pendingRequest);
    if (pending) {
      response.cookies.set(
        ZOOM_PENDING_REQUEST_COOKIE,
        encryptPendingZoomBotRequest(pending, payload.userId, secret, now),
        pendingRequestCookieOptions(isHttpsRequest(req), ZOOM_PENDING_REQUEST_TTL_SECONDS)
      );
    } else {
      response.cookies.set(
        ZOOM_PENDING_REQUEST_COOKIE,
        "",
        pendingRequestCookieOptions(isHttpsRequest(req), 0)
      );
    }
    return response;
  } catch (error) {
    return NextResponse.json(
      { error: `Failed to initialize Zoom OAuth: ${(error as Error).message}` },
      { status: 500 }
    );
  }
}

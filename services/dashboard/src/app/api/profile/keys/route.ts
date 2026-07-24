import { NextRequest, NextResponse } from "next/server";
import { getAuthenticatedUserEmail } from "@/lib/auth-utils";
import {
  HostedAdminError,
  listHostedTokens,
  mintHostedToken,
  resolveHostedUser,
  type HostedAdminConfig,
  type HostedMintInput,
} from "@/lib/hosted-account-admin";

const getAdminConfig = () => {
  const VEXA_ADMIN_API_URL = process.env.VEXA_ADMIN_API_URL || "";
  const VEXA_ADMIN_API_KEY = process.env.VEXA_ADMIN_API_KEY || "";
  return { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY };
};

function adminConfig(): HostedAdminConfig | null {
  const { VEXA_ADMIN_API_URL, VEXA_ADMIN_API_KEY } = getAdminConfig();
  if (!VEXA_ADMIN_API_URL || !VEXA_ADMIN_API_KEY) return null;
  return {
    baseUrl: VEXA_ADMIN_API_URL,
    adminKey: VEXA_ADMIN_API_KEY,
  };
}

function errorResponse(error: unknown): NextResponse {
  if (error instanceof HostedAdminError) {
    return NextResponse.json(
      {
        error: {
          code: error.code,
          message: error.message,
        },
      },
      { status: error.status }
    );
  }
  return NextResponse.json(
    {
      error: {
        code: "SERVICE_UNAVAILABLE",
        message: "The Account service is temporarily unavailable.",
      },
    },
    { status: 503 }
  );
}

/**
 * GET /api/profile/keys — list user's API keys via admin API
 */
export async function GET() {
  const config = adminConfig();
  if (!config) {
    return NextResponse.json(
      {
        error: {
          code: "NOT_CONFIGURED",
          message: "The Account service is not configured.",
        },
      },
      { status: 503 }
    );
  }

  const email = await getAuthenticatedUserEmail();
  if (!email) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  try {
    const user = await resolveHostedUser(config, email);
    const keys = await listHostedTokens(config, user.id);
    return NextResponse.json({ keys });
  } catch (error) {
    return errorResponse(error);
  }
}

/**
 * POST /api/profile/keys — create a new API key via admin API
 */
export async function POST(request: NextRequest) {
  const config = adminConfig();
  if (!config) {
    return NextResponse.json(
      {
        error: {
          code: "NOT_CONFIGURED",
          message: "The Account service is not configured.",
        },
      },
      { status: 503 }
    );
  }

  const email = await getAuthenticatedUserEmail();
  if (!email) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  try {
    const body = await request.json() as HostedMintInput;
    const user = await resolveHostedUser(config, email);
    const data = await mintHostedToken(config, user.id, body);
    return NextResponse.json(data, { status: 201 });
  } catch (error) {
    return errorResponse(error);
  }
}

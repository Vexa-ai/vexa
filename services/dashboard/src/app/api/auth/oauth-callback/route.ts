import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth/next";
import { authOptions } from "../[...nextauth]/route";

/**
 * OAuth callback endpoint - syncs user info after Google OAuth
 * Called by the frontend after successful OAuth to get user info
 */
export async function GET(request: NextRequest) {
  try {
    const session = await getServerSession(authOptions);

    if (!session || !(session as any).vexaUser) {
      return NextResponse.json(
        { error: "Not authenticated via OAuth" },
        { status: 401 }
      );
    }

    const vexaUser = (session as any).vexaUser;
    const token = (session as any).vexaToken;

    // Return user info in the same format as the login endpoint
    return NextResponse.json({
      user: {
        id: vexaUser.id,
        email: vexaUser.email,
        name: vexaUser.name,
        max_concurrent_bots: vexaUser.max_concurrent_bots,
        created_at: vexaUser.created_at,
      },
      token,
      isNewUser: (session as any).isNewUser || false,
    });
  } catch (error) {
    console.error("OAuth callback error:", error);
    return NextResponse.json(
      { error: "Failed to sync OAuth session" },
      { status: 500 }
    );
  }
}


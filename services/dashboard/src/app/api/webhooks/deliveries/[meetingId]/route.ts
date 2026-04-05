import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

/**
 * GET /api/webhooks/deliveries/:meetingId
 *
 * Proxy to admin-api for meeting-specific webhook delivery attempts.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ meetingId: string }> }
) {
  const VEXA_API_URL = process.env.VEXA_API_URL || "http://localhost:18056";
  const cookieStore = await cookies();
  const userToken = cookieStore.get("vexa-token")?.value;
  const apiKey = userToken || process.env.VEXA_API_KEY || "";
  const { meetingId } = await params;

  try {
    const response = await fetch(
      `${VEXA_API_URL}/admin/webhooks/deliveries/${meetingId}`,
      {
        headers: {
          "Content-Type": "application/json",
          ...(apiKey ? { "X-API-Key": apiKey } : {}),
        },
      }
    );

    if (response.status === 404) {
      return NextResponse.json({ attempts: [] });
    }

    if (!response.ok) {
      return NextResponse.json(
        { error: "Failed to fetch meeting webhook deliveries" },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ attempts: [] });
  }
}

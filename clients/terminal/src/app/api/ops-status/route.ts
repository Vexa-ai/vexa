/** GET /api/ops-status — is a maintenance window active? Public (a notice is for everyone,
 *  including the login screen), read-only, served straight off the mounted file so it keeps
 *  working while the backend stack is mid-deploy. */
import { NextResponse } from "next/server";
import { readOpsStatus } from "./status";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(readOpsStatus(), {
    headers: { "Cache-Control": "no-store, no-cache, must-revalidate" },
  });
}

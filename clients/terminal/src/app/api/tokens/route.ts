/** The logged-in user's API tokens — GET lists, POST mints (the token value crosses ONCE, in the
 *  POST response, and is never retrievable again).
 *
 *  admin-api's token endpoints are ADMIN-tier, so these routes call it with the server's
 *  VEXA_ADMIN_API_KEY (the same way /api/auth/login does) and scope EVERY operation to the user_id
 *  resolved from the auth cookies (currentUser.ts) — a user_id from the client is never accepted (P20).
 */
import { NextResponse, type NextRequest } from "next/server";
import { listUserTokens, mintUserToken } from "../auth/adminApi";
import { currentUser } from "./currentUser";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;
const VALID_SCOPES = new Set(["bot", "tx", "browser"]);

export async function GET() {
  const me = await currentUser();
  if (!me.ok) return NextResponse.json({ error: me.error }, { status: me.status, headers: NO_STORE });

  const listed = await listUserTokens(me.userId);
  if (!listed.ok) {
    return NextResponse.json({ error: listed.error || "Failed to list tokens" }, { status: listed.status || 502, headers: NO_STORE });
  }
  return NextResponse.json({ tokens: listed.data ?? [] }, { headers: NO_STORE });
}

export async function POST(request: NextRequest) {
  const me = await currentUser();
  if (!me.ok) return NextResponse.json({ error: me.error }, { status: me.status, headers: NO_STORE });

  let body: { scopes?: unknown; name?: unknown; expiresIn?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400, headers: NO_STORE });
  }

  const scopes = Array.isArray(body.scopes) ? body.scopes.filter((s): s is string => typeof s === "string") : [];
  if (scopes.length === 0 || scopes.some((s) => !VALID_SCOPES.has(s))) {
    return NextResponse.json({ error: `Scopes must be a non-empty subset of ${[...VALID_SCOPES].join(", ")}` }, { status: 400, headers: NO_STORE });
  }
  const name = typeof body.name === "string" && body.name.trim() ? body.name.trim().slice(0, 255) : undefined;
  const expiresIn = typeof body.expiresIn === "number" && Number.isFinite(body.expiresIn) && body.expiresIn > 0
    ? Math.floor(body.expiresIn)
    : undefined;

  const minted = await mintUserToken(me.userId, { scopes, name, expiresIn });
  if (!minted.ok || !minted.data?.token) {
    return NextResponse.json({ error: minted.error || "Failed to mint token" }, { status: minted.status || 502, headers: NO_STORE });
  }
  // The one and only time the secret crosses to the client.
  return NextResponse.json({ token: minted.data }, { status: 201, headers: NO_STORE });
}

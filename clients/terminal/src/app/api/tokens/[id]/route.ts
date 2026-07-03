/** DELETE /api/tokens/{id} — revoke ONE of the logged-in user's own tokens.
 *
 *  admin-api's DELETE /admin/tokens/{id} is admin-tier and unscoped, so ownership is enforced HERE:
 *  the id must appear in the user's own token list (resolved from the auth cookies) before the revoke
 *  is forwarded — otherwise 404, indistinguishable from a nonexistent token (no cross-user probing).
 */
import { NextResponse } from "next/server";
import { listUserTokens, revokeToken } from "../../auth/adminApi";
import { currentUser } from "../currentUser";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

export async function DELETE(_request: Request, ctx: { params: Promise<{ id: string }> }) {
  const me = await currentUser();
  if (!me.ok) return NextResponse.json({ error: me.error }, { status: me.status, headers: NO_STORE });

  const { id } = await ctx.params;
  const tokenId = Number(id);
  if (!Number.isInteger(tokenId)) {
    return NextResponse.json({ error: "Invalid token id" }, { status: 400, headers: NO_STORE });
  }

  const listed = await listUserTokens(me.userId);
  if (!listed.ok) {
    return NextResponse.json({ error: listed.error || "Failed to verify token ownership" }, { status: listed.status || 502, headers: NO_STORE });
  }
  if (!(listed.data ?? []).some((t) => t.id === tokenId)) {
    return NextResponse.json({ error: "Token not found" }, { status: 404, headers: NO_STORE });
  }

  const revoked = await revokeToken(tokenId);
  if (!revoked.ok) {
    return NextResponse.json({ error: revoked.error || "Failed to revoke token" }, { status: revoked.status || 502, headers: NO_STORE });
  }
  return NextResponse.json({ success: true }, { headers: NO_STORE });
}

/** Admin who-am-I — the surface's visibility probe. 200 {admin:true} only for a VERIFIED
 *  allowlisted admin (see ../gate.ts); everyone else gets a plain 404, indistinguishable from
 *  the route not existing, so the panel stays hidden. */
import { NextResponse } from "next/server";
import { requireAdmin } from "../gate";

export const dynamic = "force-dynamic";

export async function GET() {
  const admin = await requireAdmin();
  if (!admin) return new NextResponse(null, { status: 404 });
  return NextResponse.json(
    { admin: true, email: admin.email },
    { headers: { "Cache-Control": "no-store" } },
  );
}

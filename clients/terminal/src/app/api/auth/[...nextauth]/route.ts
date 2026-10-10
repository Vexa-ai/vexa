/** NextAuth catch-all handler. Config lives in ./authOptions (an App Router route.ts may only export
 *  HTTP handlers). See authOptions.ts for why NextAuth is only the OAuth broker here. */
import NextAuth from "next-auth";
import { authOptions } from "./authOptions";

const handler = NextAuth(authOptions);

import { NextRequest } from "next/server";
import { connectionCallback } from "../../connections/callback";
export async function GET(req: NextRequest, context: any) {
  if (req.nextUrl.pathname === '/api/auth/callback/google' && req.nextUrl.searchParams.get('state')?.startsWith('vxc_')) return connectionCallback(req);
  return handler(req, context);
}
export { handler as POST };

/** Every terminal page is served with a per-response nonce and the Content-Security-Policy built
 *  from it (app/contentSecurityPolicy.ts). Next reads the nonce from the request's policy header and
 *  stamps its own scripts with it; the root layout reads `x-nonce` for the scripts it writes.
 *  API routes, static assets and the /b/ proxy are not pages and are left alone. */
import { NextResponse, type NextRequest } from "next/server";
import { NONCE_HEADER, contentSecurityPolicy, newNonce } from "./app/contentSecurityPolicy";

export function middleware(request: NextRequest) {
  const nonce = newNonce();
  const csp = contentSecurityPolicy(nonce);
  const headers = new Headers(request.headers);
  headers.set(NONCE_HEADER, nonce);
  headers.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!api/|_next/static|_next/image|b/|favicon.ico|icon.svg|.*\\.(?:png|jpg|jpeg|gif|svg|webp|ico|woff2?|txt|xml|json)$).*)",
      missing: [{ type: "header", key: "next-router-prefetch" }],
    },
  ],
};

/** Every terminal page is served with a per-response nonce and the Content-Security-Policy built
 *  from it (app/contentSecurityPolicy.ts). Next reads the nonce from the request's policy header and
 *  stamps its own scripts with it; the root layout reads `x-nonce` for the scripts it writes.
 *  The policy's foreign sources come from this process's environment on every request (LIVEKIT_URL
 *  at run time; NEXT_PUBLIC_GA_MEASUREMENT_ID as built), so they follow the deployment's own config.
 *  The Meeting Canvas frame (`/canvas-frame`) gets its own policy, which sandboxes the document.
 *  API routes, static assets and the /b/ proxy are not pages and are left alone. */
import { NextResponse, type NextRequest } from "next/server";
import { NONCE_HEADER, canvasFrameContentSecurityPolicy, contentSecurityPolicy, newNonce } from "./app/contentSecurityPolicy";
import { CANVAS_FRAME_PATH } from "./canvas/frame/protocol";

export function middleware(request: NextRequest) {
  const nonce = newNonce();
  // The Meeting Canvas frame runs workspace-written code: its own, sandboxing policy.
  const csp = request.nextUrl.pathname === CANVAS_FRAME_PATH
    ? canvasFrameContentSecurityPolicy(nonce)
    : contentSecurityPolicy(nonce);
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

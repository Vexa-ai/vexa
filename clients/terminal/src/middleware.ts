/** Every terminal page is served with a per-response nonce and the Content-Security-Policy built
 *  from it (app/contentSecurityPolicy.ts). Next reads the nonce from the request's policy header and
 *  stamps its own scripts with it; the root layout reads `x-nonce` for the scripts it writes.
 *  The policy's foreign sources come from this process's environment on every request (LIVEKIT_URL
 *  at run time; NEXT_PUBLIC_GA_MEASUREMENT_ID as built), so they follow the deployment's own config.
 *  The Meeting Canvas frame (`/canvas-frame`) gets its own policy, which sandboxes the document.
 *  Left alone, by exact path and never by extension (a page URL may end in .json or .svg, as a
 *  document link does): API routes, the /b/ proxy, Next's build assets and image optimiser, and the
 *  real files this app serves at the root — `public/` and the app's metadata files, named one by
 *  one, and `public/mediapipe/`, which the build writes (scripts/copy-mediapipe.mjs). */
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
  // The canvas frame names no host it may resolve, so a link in a view leaks nothing through DNS.
  if (request.nextUrl.pathname === CANVAS_FRAME_PATH) response.headers.set("X-DNS-Prefetch-Control", "off");
  return response;
}

// Next reads `config` statically, so the matcher is a literal. The files it names are every file in
// `public/` and the app directory's root metadata files (`app/icon.svg`), and the `mediapipe/` tree
// the build writes into `public/`; contentSecurityPolicy.test.ts holds the list to the tree.
export const config = {
  matcher: [
    {
      source: "/((?!api/|b/|mediapipe(?:/|$)|_next/static/|_next/image$|(?:README\\.md|vexa-logo\\.svg|icon\\.svg)$).*)",
      missing: [{ type: "header", key: "next-router-prefetch" }],
    },
  ],
};

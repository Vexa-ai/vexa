/** `GET /api/join/preview?i=<token>` — what the invite IS, before anyone signs in.
 *
 *  The one call on this terminal that goes to agent-api WITHOUT a user key, and the reason is the
 *  same reason agent-api's own `GET /api/workspace/invites/preview` takes no subject: the invite
 *  card renders for somebody who has no account here yet (Vexa-ai/vexa#1635). The gateway edge every
 *  other proxy uses resolves an X-API-Key into a user, and a visitor who just clicked a colleague's
 *  link has none — sending them through it would answer 401 and the page would have to ask them to
 *  sign in to find out what they were signing in for. That reads as a phish, and it is also the
 *  wrong order: say what this is, then ask who they are.
 *
 *  WHAT KEEPS IT SAFE is the token, and it is the same capability agent-api already trusts: whoever
 *  holds the link may see the workspace's name, the role, who shared it, and — for a bound invite —
 *  the address it admits (the page prefills and locks that field). Nothing here grants anything; the
 *  REDEEM is `POST /api/workspace/invites/accept` through the ordinary authenticated proxy, where
 *  the gateway's verified email is what enforces the binding. A token that matches nothing gets
 *  agent-api's 404 passed through verbatim, so this route never enumerates workspaces either.
 *
 *  `adminApi.ts` already reaches agent-api directly for the internal tier; this is the same hop
 *  without the secret, because a capability-gated read needs no other credential.
 */
import { NextResponse, type NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

export async function GET(request: NextRequest) {
  const token = request.nextUrl.searchParams.get("i") || "";
  if (!token) {
    return NextResponse.json({ error: "no_token" }, { status: 400, headers: NO_STORE });
  }
  const base = (process.env.AGENT_API_URL || "").replace(/\/$/, "");
  if (!base) {
    // Not a 404: the invite may be perfectly good and this deployment simply cannot ask about it.
    // The page turns this into "could not be checked just now", which is what actually happened.
    console.error("[terminal-join] AGENT_API_URL is not set — an invite cannot be previewed");
    return NextResponse.json({ error: "unconfigured" }, { status: 503, headers: NO_STORE });
  }
  try {
    const upstream = await fetch(
      `${base}/api/workspace/invites/preview?token=${encodeURIComponent(token)}`,
      { cache: "no-store" },
    );
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json", ...NO_STORE },
    });
  } catch (err) {
    console.error("[terminal-join] invite preview failed", err);
    return NextResponse.json({ error: "upstream_unavailable" }, { status: 502, headers: NO_STORE });
  }
}

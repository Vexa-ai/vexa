/** Direct email login — DEVELOPMENT ONLY. POST {email} → find-or-create the user at admin-api,
 *  mint an APIToken (scopes bot,tx,browser), set the httpOnly `vexa-token` + `vexa-user-info` cookies.
 *
 *  ⚠ THIS ROUTE IS DEAD IN PRODUCTION, and that is the point of it now. It used to accept any address
 *  containing "test" on ANY deploy — and, in minutes/meetings mode, any address at all — which made a
 *  production terminal password-less: whoever could POST here became whoever they named. Production
 *  sign-in is the emailed MAGIC LINK (`request-link/` → `redeem/`, where control of the mailbox is the
 *  proof) or OAuth (`[...nextauth]/`). What survives here is local dev tooling and the harnesses that
 *  drive a dev container; against a NODE_ENV=production container it answers 403 before it reads
 *  anything. To sign in against a deployed container, ask for a link and redeem it.
 *
 *  Must never be cached — a cached response would pin one identity for every subsequent login.
 */
import { NextResponse, type NextRequest } from "next/server";
import { cookies } from "next/headers";
import { AUTH_COOKIE, USER_INFO_COOKIE, findOrCreateUserToken, mintFirstVisitScaffold } from "../adminApi";
import { SIGNIN_NOT_ALLOWED, SIGNIN_UNAVAILABLE } from "../../../signinRefusal";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function isSecureRequest(): boolean {
  return (
    (process.env.TERMINAL_URL || "").startsWith("https://") ||
    (process.env.NEXTAUTH_URL || "").startsWith("https://") ||
    false
  );
}

export async function POST(request: NextRequest) {
  // The gate, before anything else is read: outside a development build this route mints nothing.
  if (process.env.NODE_ENV !== "development") {
    return NextResponse.json(
      {
        error:
          "Direct email login is disabled here. Request an emailed sign-in link (POST /api/auth/request-link) or use OAuth.",
      },
      { status: 403, headers: NO_STORE },
    );
  }

  let email: unknown;
  try {
    ({ email } = await request.json());
  } catch {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400, headers: NO_STORE });
  }

  if (typeof email !== "string" || !email.trim()) {
    return NextResponse.json({ error: "Email is required" }, { status: 400, headers: NO_STORE });
  }
  const normalized = email.trim().toLowerCase();
  if (!EMAIL_RE.test(normalized)) {
    return NextResponse.json({ error: "Invalid email format" }, { status: 400, headers: NO_STORE });
  }

  // Dev-only as this door is, it still ADMITS the way every other door does (Vexa-ai/vexa#1783):
  // `findOrCreateUserToken` asks first, so a development host reachable from outside — the hot
  // overlay runs NODE_ENV=development — cannot mint accounts for whoever posts here.
  const result = await findOrCreateUserToken(normalized);
  if (!result.ok) {
    const error = result.refused === "not-allowed" ? SIGNIN_NOT_ALLOWED
      : result.refused === "unavailable" ? SIGNIN_UNAVAILABLE
      : result.error;
    return NextResponse.json({ error }, { status: result.status || 500, headers: NO_STORE });
  }

  const { user, token } = result;
  const secure = isSecureRequest();
  const cookieStore = await cookies();
  const opts = { httpOnly: true, secure, sameSite: "lax" as const, maxAge: 60 * 60 * 24 * 30, path: "/" };
  cookieStore.set(AUTH_COOKIE, token, opts);
  cookieStore.set(USER_INFO_COOKIE, JSON.stringify({ id: user.id, email: user.email, name: user.name || user.email.split("@")[0] }), opts);

  // THE ARRIVAL (F42) — the same record the magic-link door mints, handed back rather than
  // redirected to, because this door answers a fetch() and its caller owns the navigation. A failed
  // mint is logged and omitted: the sign-in itself is not held hostage to it, and a caller with no
  // `url` lands where it always did.
  const minted = await mintFirstVisitScaffold(user.email, user.id);
  if (!minted.ok || !minted.data?.url) {
    console.error(`[terminal-auth] first-visit scaffold mint failed for ${user.email}: ${minted.ok ? "no url" : minted.error}`);
  }
  return NextResponse.json(
    {
      success: true,
      user: { id: user.id, email: user.email, name: user.name ?? user.email },
      ...(minted.ok && minted.data?.url ? { url: minted.data.url } : {}),
    },
    { headers: NO_STORE },
  );
}

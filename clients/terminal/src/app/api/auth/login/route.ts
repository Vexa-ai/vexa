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
import { AUTH_COOKIE, USER_INFO_COOKIE, findOrCreateUserToken } from "../adminApi";

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

  const result = await findOrCreateUserToken(normalized);
  if (!result.ok) {
    return NextResponse.json({ error: result.error }, { status: result.status || 500, headers: NO_STORE });
  }

  const { user, token } = result;
  const secure = isSecureRequest();
  const cookieStore = await cookies();
  const opts = { httpOnly: true, secure, sameSite: "lax" as const, maxAge: 60 * 60 * 24 * 30, path: "/" };
  cookieStore.set(AUTH_COOKIE, token, opts);
  cookieStore.set(USER_INFO_COOKIE, JSON.stringify({ id: user.id, email: user.email, name: user.name || user.email.split("@")[0] }), opts);

  return NextResponse.json(
    { success: true, user: { id: user.id, email: user.email, name: user.name ?? user.email } },
    { headers: NO_STORE },
  );
}

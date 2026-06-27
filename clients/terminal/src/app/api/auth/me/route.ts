/** Who-am-I — reads the httpOnly `vexa-user-info` cookie (set at login). The login gate calls this to
 *  decide whether to show the email-entry form. No backend round-trip: presence of the auth + user-info
 *  cookies means authenticated. */
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { AUTH_COOKIE, USER_INFO_COOKIE } from "../adminApi";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

export async function GET() {
  const cookieStore = await cookies();
  const token = cookieStore.get(AUTH_COOKIE)?.value;
  const info = cookieStore.get(USER_INFO_COOKIE)?.value;

  if (!token) {
    return NextResponse.json({ authenticated: false }, { status: 401, headers: NO_STORE });
  }

  let email: string | undefined;
  if (info) {
    try {
      email = (JSON.parse(info) as { email?: string }).email;
    } catch {
      /* malformed cookie — still authenticated by the token, just no email to show */
    }
  }

  return NextResponse.json({ authenticated: true, user: { email: email ?? null } }, { headers: NO_STORE });
}

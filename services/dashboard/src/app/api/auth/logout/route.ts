import { NextResponse } from "next/server";
import { cookies } from "next/headers";

export async function POST() {
  const cookieStore = await cookies();
  const domain = process.env.COOKIE_DOMAIN;

  // Delete vexa-token and vexa-user-info (with domain if set for SSO)
  if (domain) {
    cookieStore.set("vexa-token", "", { maxAge: 0, path: "/", domain });
    cookieStore.set("vexa-user-info", "", { maxAge: 0, path: "/", domain });
  } else {
    cookieStore.delete("vexa-token");
    cookieStore.delete("vexa-user-info");
  }

  // Also clear NextAuth session cookie if it exists
  cookieStore.delete("next-auth.session-token");
  cookieStore.delete("__Secure-next-auth.session-token");

  return NextResponse.json({ success: true });
}

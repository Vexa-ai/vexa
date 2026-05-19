import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { getAuthCookieName, getUserInfoCookieName } from "@/lib/auth-cookies";

export async function POST() {
  const cookieStore = await cookies();
  cookieStore.delete(getAuthCookieName());
  cookieStore.delete(getUserInfoCookieName());
  // Also clear NextAuth session cookie if it exists
  cookieStore.delete("next-auth.session-token");
  cookieStore.delete("__Secure-next-auth.session-token");

  return NextResponse.json({ success: true });
}

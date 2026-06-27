/** Logout — clears the terminal's auth cookies. No backend call. */
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { AUTH_COOKIE, USER_INFO_COOKIE } from "../adminApi";

export const dynamic = "force-dynamic";

export async function POST() {
  const cookieStore = await cookies();
  cookieStore.delete(AUTH_COOKIE);
  cookieStore.delete(USER_INFO_COOKIE);
  return NextResponse.json({ success: true }, { headers: { "Cache-Control": "no-store" } });
}

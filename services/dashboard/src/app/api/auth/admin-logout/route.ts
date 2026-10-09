import { NextResponse } from "next/server";
import { cookies } from "next/headers";

const ADMIN_COOKIE_NAME = "vexa-admin-session";

export async function POST() {
  try {
    const cookieStore = await cookies();

    // Clear the admin session cookie
    cookieStore.delete(ADMIN_COOKIE_NAME);

    return NextResponse.json({
      success: true,
      message: "Admin session cleared",
    });
  } catch (error) {
    console.error("Admin logout error:", error);
    return NextResponse.json(
      { error: "Logout failed" },
      { status: 500 }
    );
  }
}

import { NextResponse } from "next/server";

/**
 * GET /api/notifications
 *
 * Returns active notifications (maintenance, incidents, announcements).
 * In production this would fetch from a blog/CMS API or admin-configured store.
 * For now, returns from environment variable or empty array.
 */
export async function GET() {
  // Check for configured notifications source
  const notificationsUrl = process.env.NOTIFICATIONS_URL;

  if (notificationsUrl) {
    try {
      const response = await fetch(notificationsUrl, {
        next: { revalidate: 300 }, // Cache for 5 minutes
      });
      if (response.ok) {
        const data = await response.json();
        return NextResponse.json(data);
      }
    } catch {
      // Fall through to empty response
    }
  }

  // Default: no notifications
  return NextResponse.json({ notifications: [] });
}
